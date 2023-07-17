# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

r"""Handler for `upgrade` relation events for in-place upgrades on VMs."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Iterable, List, Literal, Optional, Tuple

from ops.charm import (
    ActionEvent,
    CharmBase,
    CharmEvents,
    RelationCreatedEvent,
    UpgradeCharmEvent,
)
from ops.framework import EventBase, EventSource, Object
from ops.model import Relation, Unit
from pydantic import BaseModel, root_validator, validator

# The unique Charmhub library identifier, never change it
LIBID = "156258aefb79435a93d933409a8c8684"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 6

PYDEPS = ["pydantic>=1.10,<2"]

logger = logging.getLogger(__name__)

# --- DEPENDENCY RESOLUTION FUNCTIONS ---


def build_complete_sem_ver(version: str) -> list[int]:
    """Builds complete major.minor.patch version from version string.

    Returns:
        List of major.minor.patch version integers
    """
    versions = [int(ver) if ver != "*" else 0 for ver in str(version).split(".")]

    # padding with 0s until complete major.minor.patch
    return (versions + 3 * [0])[:3]


def verify_caret_requirements(version: str, requirement: str) -> bool:
    """Verifies version requirements using carats.

    Args:
        version: the version currently in use
        requirement: the requirement version

    Returns:
        True if `version` meets defined `requirement`. Otherwise False
    """
    if not requirement.startswith("^"):
        return True

    requirement = requirement[1:]

    sem_version = build_complete_sem_ver(version)
    sem_requirement = build_complete_sem_ver(requirement)

    # caret uses first non-zero character, not enough to just count '.
    max_version_index = requirement.count(".")
    for i, semver in enumerate(sem_requirement):
        if semver != 0:
            max_version_index = i
            break

    for i in range(3):
        # version higher than first non-zero
        if (i < max_version_index) and (sem_version[i] > sem_requirement[i]):
            return False

        # version either higher or lower than first non-zero
        if (i == max_version_index) and (sem_version[i] != sem_requirement[i]):
            return False

        # valid
        if (i > max_version_index) and (sem_version[i] > sem_requirement[i]):
            return True

    return False


def verify_tilde_requirements(version: str, requirement: str) -> bool:
    """Verifies version requirements using tildes.

    Args:
        version: the version currently in use
        requirement: the requirement version

    Returns:
        True if `version` meets defined `requirement`. Otherwise False
    """
    if not requirement.startswith("~"):
        return True

    requirement = requirement[1:]

    sem_version = build_complete_sem_ver(version)
    sem_requirement = build_complete_sem_ver(requirement)

    max_version_index = min(1, requirement.count("."))

    for i in range(3):
        # version higher before requirement level
        if (i < max_version_index) and (sem_version[i] > sem_requirement[i]):
            return False

        # version either higher or lower at requirement level
        if (i == max_version_index) and (sem_version[i] != sem_requirement[i]):
            return False

        # version lower after requirement level
        if (i > max_version_index) and (sem_version[i] < sem_requirement[i]):
            return False

    # must be valid
    return True


def verify_wildcard_requirements(version: str, requirement: str) -> bool:
    """Verifies version requirements using wildcards.

    Args:
        version: the version currently in use
        requirement: the requirement version

    Returns:
        True if `version` meets defined `requirement`. Otherwise False
    """
    if "*" not in requirement:
        return True

    sem_version = build_complete_sem_ver(version)
    sem_requirement = build_complete_sem_ver(requirement)

    max_version_index = requirement.count(".")

    for i in range(3):
        # version not the same before wildcard
        if (i < max_version_index) and (sem_version[i] != sem_requirement[i]):
            return False

        # version not higher after wildcard
        if (i == max_version_index) and (sem_version[i] < sem_requirement[i]):
            return False

    # must be valid
    return True


def verify_inequality_requirements(version: str, requirement: str) -> bool:
    """Verifies version requirements using inequalities.

    Args:
        version: the version currently in use
        requirement: the requirement version

    Returns:
        True if `version` meets defined `requirement`. Otherwise False
    """
    if not any(char for char in [">", ">="] if requirement.startswith(char)):
        return True

    raw_requirement = requirement.replace(">", "").replace("=", "")

    sem_version = build_complete_sem_ver(version)
    sem_requirement = build_complete_sem_ver(raw_requirement)

    max_version_index = raw_requirement.count(".") or 0

    for i in range(3):
        # valid at same requirement level
        if (
            (i == max_version_index)
            and ("=" in requirement)
            and (sem_version[i] == sem_requirement[i])
        ):
            return True

        # version not increased at any point
        if sem_version[i] < sem_requirement[i]:
            return False

        # valid
        if sem_version[i] > sem_requirement[i]:
            return True

    # must not be valid
    return False


def verify_requirements(version: str, requirement: str) -> bool:
    """Verifies a specified version against defined requirements.

    Supports caret (`^`), tilde (`~`), wildcard (`*`) and greater-than inequalities (`>`, `>=`)

    Args:
        version: the version currently in use
        requirement: the requirement version

    Returns:
        True if `version` meets defined `requirement`. Otherwise False
    """
    if not all(
        [
            verify_inequality_requirements(version=version, requirement=requirement),
            verify_caret_requirements(version=version, requirement=requirement),
            verify_tilde_requirements(version=version, requirement=requirement),
            verify_wildcard_requirements(version=version, requirement=requirement),
        ]
    ):
        return False

    return True


# --- DEPENDENCY MODEL TYPES ---


class DependencyModel(BaseModel):
    """Manager for a single dependency.

    To be used as part of another model representing a collection of arbitrary dependencies.

    Example::

        class KafkaDependenciesModel(BaseModel):
            kafka_charm: DependencyModel
            kafka_service: DependencyModel

        deps = {
            "kafka_charm": {
                "dependencies": {"zookeeper": ">5"},
                "name": "kafka",
                "upgrade_supported": ">5",
                "version": "10",
            },
            "kafka_service": {
                "dependencies": {"zookeeper": "^3.6"},
                "name": "kafka",
                "upgrade_supported": "~3.3",
                "version": "3.3.2",
            },
        }

        model = KafkaDependenciesModel(**deps)  # loading dict in to model

        print(model.dict())  # exporting back validated deps
    """

    dependencies: dict[str, str]
    name: str
    upgrade_supported: str
    version: str

    @validator("dependencies", "upgrade_supported", each_item=True)
    @classmethod
    def dependencies_validator(cls, value):
        """Validates values with dependencies for multiple special characters."""
        if isinstance(value, dict):
            deps = value.values()
        else:
            deps = [value]

        chars = ["~", "^", ">", "*"]

        for dep in deps:
            if (count := sum([dep.count(char) for char in chars])) != 1:
                raise ValueError(
                    f"Value uses greater than 1 special character (^ ~ > *). Found {count}."
                )

        return value

    @root_validator(skip_on_failure=True)
    @classmethod
    def version_upgrade_supported_validator(cls, values):
        """Validates specified `version` meets `upgrade_supported` requirement."""
        if not verify_requirements(
            version=values.get("version"), requirement=values.get("upgrade_supported")
        ):
            raise ValueError(
                f"upgrade_supported value {values.get('upgrade_supported')} greater than version value {values.get('version')} for {values.get('name')}."
            )

        return values

    def can_upgrade(self, dependency: "DependencyModel") -> bool:
        """Compares two instances of :class:`DependencyModel` for upgradability.

        Args:
            dependency: a dependency model to compare this model against

        Returns:
            True if current model can upgrade from dependent model. Otherwise False
        """
        return verify_requirements(version=self.version, requirement=dependency.upgrade_supported)


# --- CUSTOM EXCEPTIONS ---


class UpgradeError(Exception):
    """Base class for upgrade related exceptions in the module."""

    def __init__(self, message: str, cause: Optional[str], resolution: Optional[str]):
        super().__init__(message)
        self.message = message
        self.cause = cause or ""
        self.resolution = resolution or ""

    def __repr__(self):
        """Representation of the UpgradeError class."""
        return f"{type(self).__module__}.{type(self).__name__} - {str(vars(self))}"

    def __str__(self):
        """String representation of the UpgradeError class."""
        return repr(self)


class ClusterNotReadyError(UpgradeError):
    """Exception flagging that the cluster is not ready to start upgrading.

    For example, if the cluster fails :class:`DataUpgrade._on_pre_upgrade_check_action`

    Args:
        message: string message to be logged out
        cause: short human-readable description of the cause of the error
        resolution: short human-readable instructions for manual error resolution (optional)
    """

    def __init__(self, message: str, cause: str, resolution: Optional[str] = None):
        super().__init__(message, cause=cause, resolution=resolution)


class VersionError(UpgradeError):
    """Exception flagging that the old `version` fails to meet the new `upgrade_supported`s.

    For example, upgrades from version `2.x` --> `4.x`,
        but `4.x` only supports upgrading from `3.x` onwards

    Args:
        message: string message to be logged out
        cause: short human-readable description of the cause of the error
        resolution: short human-readable instructions for manual solutions to the error (optional)
    """

    def __init__(self, message: str, cause: str, resolution: Optional[str] = None):
        super().__init__(message, cause=cause, resolution=resolution)


class DependencyError(UpgradeError):
    """Exception flagging that some new `dependency` is not being met.

    For example, new version requires related App version `2.x`, but currently is `1.x`

    Args:
        message: string message to be logged out
        cause: short human-readable description of the cause of the error
        resolution: short human-readable instructions for manual solutions to the error (optional)
    """

    def __init__(self, message: str, cause: str, resolution: Optional[str] = None):
        super().__init__(message, cause=cause, resolution=resolution)


# --- CUSTOM EVENTS ---


class UpgradeGrantedEvent(EventBase):
    """Used to tell units that they can process an upgrade.

    Handlers of this event must meet the following:
        - SHOULD check for related application deps from :class:`DataUpgrade.dependencies`
            - MAY raise :class:`DependencyError` if dependency not met
        - MUST update unit `state` after validating the success of the upgrade, calling one of:
            - :class:`DataUpgrade.set_unit_failed` if the unit upgrade fails
            - :class:`DataUpgrade.set_unit_completed` if the unit upgrade succeeds
        - MUST call :class:`DataUpgarde.on_upgrade_changed` on exit so event not lost on leader
    """


class UpgradeFinishedEvent(EventBase):
    """Used to tell units that they finished the upgrade.

    Handlers of this event must meet the following:
        - MUST trigger the upgrade in the next unit by, for example, decrementing the partition
            value from the rolling update strategy
        - MUST update unit `state` if the previous operation fails, calling
            :class:`DataUpgrade.set_unit_failed`
    """


class UpgradeEvents(CharmEvents):
    """Upgrade events.

    This class defines the events that the lib can emit.
    """

    upgrade_granted = EventSource(UpgradeGrantedEvent)
    upgrade_finished = EventSource(UpgradeFinishedEvent)


# --- EVENT HANDLER ---


class DataUpgrade(Object, ABC):
    """Manages `upgrade` relation operators for in-place upgrades."""

    STATES = ["failed", "idle", "ready", "upgrading", "completed"]

    on = UpgradeEvents()  # pyright: ignore [reportGeneralTypeIssues]

    def __init__(
        self,
        charm: CharmBase,
        dependency_model: BaseModel,
        relation_name: str = "upgrade",
        substrate: Literal["vm", "k8s"] = "vm",
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.dependency_model = dependency_model
        self.relation_name = relation_name
        self.substrate = substrate
        self._upgrade_stack = None

        # events
        self.framework.observe(
            self.charm.on[relation_name].relation_created, self._on_upgrade_created
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed, self.on_upgrade_changed
        )
        self.framework.observe(self.charm.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(getattr(self.on, "upgrade_granted"), self._on_upgrade_granted)
        self.framework.observe(getattr(self.on, "upgrade_finished"), self._on_upgrade_finished)

        # actions
        self.framework.observe(
            getattr(self.charm.on, "pre_upgrade_check_action"), self._on_pre_upgrade_check_action
        )

    @property
    def peer_relation(self) -> Optional[Relation]:
        """The upgrade peer relation."""
        return self.charm.model.get_relation(self.relation_name)

    @property
    def app_units(self) -> Iterable[Unit]:
        """The peer-related units in the application."""
        if not self.peer_relation:
            return []

        return set([self.charm.unit] + list(self.peer_relation.units))

    @property
    def state(self) -> Optional[str]:
        """The unit state from the upgrade peer relation."""
        if not self.peer_relation:
            return None

        return self.peer_relation.data[self.charm.unit].get("state", None)

    @property
    def stored_dependencies(self) -> Optional[BaseModel]:
        """The application dependencies from the upgrade peer relation."""
        if not self.peer_relation:
            return None

        if not (deps := self.peer_relation.data[self.charm.app].get("dependencies", "")):
            return None

        return type(self.dependency_model)(**json.loads(deps))

    @property
    def upgrade_stack(self) -> Optional[List[int]]:
        """Gets the upgrade stack from the upgrade peer relation.

        Unit.ids are ordered Last-In-First-Out (LIFO).
            i.e unit.id at index `-1` is the first unit to upgrade.
            unit.id at index `0` is the last unit to upgrade.

        Returns:
            List of integer unit.ids, ordered in upgrade order in a stack
        """
        if not self.peer_relation:
            return None

        # lazy-load
        if self._upgrade_stack is None:
            self._upgrade_stack = (
                json.loads(self.peer_relation.data[self.charm.app].get("upgrade-stack", "[]"))
                or None
            )

        return self._upgrade_stack

    @upgrade_stack.setter
    def upgrade_stack(self, stack: List[int]) -> None:
        """Sets the upgrade stack to the upgrade peer relation.

        Unit.ids are ordered Last-In-First-Out (LIFO).
            i.e unit.id at index `-1` is the first unit to upgrade.
            unit.id at index `0` is the last unit to upgrade.
        """
        if not self.peer_relation:
            return

        self.peer_relation.data[self.charm.app].update({"upgrade-stack": json.dumps(stack)})
        self._upgrade_stack = stack

    @property
    def cluster_state(self) -> Optional[str]:
        """Current upgrade state for cluster units.

        Determined from :class:`DataUpgrade.STATE`, taking the lowest ordinal unit state.

        For example, if units in have states: `["ready", "upgrading", "completed"]`,
            the overall state for the cluster is `ready`.

        Returns:
            String of upgrade state from the furthest behind unit.
        """
        if not self.peer_relation:
            return None

        states = [self.peer_relation.data[unit].get("state", "") for unit in self.app_units]

        try:
            return sorted(states, key=self.STATES.index)[0]
        except (ValueError, KeyError):
            return None

    @abstractmethod
    def pre_upgrade_check(self) -> None:
        """Runs necessary checks validating the cluster is in a healthy state to upgrade.

        Called by all units during :meth:`_on_pre_upgrade_check_action`.

        Raises:
            :class:`ClusterNotReadyError`: if cluster is not ready to upgrade
        """
        pass

    def build_upgrade_stack(self) -> List[int]:
        """Builds ordered iterable of all application unit.ids to upgrade in.

        Called by leader unit during :meth:`_on_pre_upgrade_check_action`.

        Returns:
            Iterable of integer unit.ids, LIFO ordered in upgrade order
                i.e `[5, 2, 4, 1, 3]`, unit `3` upgrades first, `5` upgrades last
        """
        # don't raise if k8s substrate, uses default statefulset order
        if self.substrate == "k8s":
            return []

        raise NotImplementedError

    @abstractmethod
    def log_rollback_instructions(self) -> None:
        """Sets charm state and logs out rollback instructions.

        Called by all units when `state=failed` found during :meth:`_on_upgrade_changed`.
        """
        pass

    def set_unit_failed(self) -> None:
        """Sets unit `state=failed` to the upgrade peer data."""
        if not self.peer_relation:
            return None

        # needed to refresh the stack
        # now leader pulls a fresh stack from newly updated relation data
        if self.charm.unit.is_leader():
            self._upgrade_stack = None

        self.peer_relation.data[self.charm.unit].update({"state": "failed"})

        if self.substrate == "k8s":
            self.on_upgrade_changed(EventBase(self.handle))

    def set_unit_completed(self) -> None:
        """Sets unit `state=completed` to the upgrade peer data."""
        if not self.peer_relation:
            return None

        # needed to refresh the stack
        # now leader pulls a fresh stack from newly updated relation data
        if self.charm.unit.is_leader():
            self._upgrade_stack = None

        self.peer_relation.data[self.charm.unit].update({"state": "completed"})

        if self.substrate == "k8s":
            self.on_upgrade_changed(EventBase(self.handle))

    def _on_upgrade_created(self, event: RelationCreatedEvent) -> None:
        """Handler for `upgrade-relation-created` events."""
        if not self.peer_relation:
            event.defer()
            return

        # setting initial idle state needed to avoid execution on upgrade-changed events
        self.peer_relation.data[self.charm.unit].update({"state": "idle"})

        if self.charm.unit.is_leader():
            logger.debug("Persisting dependencies to upgrade relation data...")
            self.peer_relation.data[self.charm.app].update(
                {"dependencies": json.dumps(self.dependency_model.dict())}
            )

    def _on_pre_upgrade_check_action(self, event: ActionEvent) -> None:
        """Handler for `pre-upgrade-check-action` events."""
        if not self.peer_relation:
            event.fail(message="Could not find upgrade relation.")
            return

        if not self.charm.unit.is_leader():
            event.fail(message="Action must be ran on the Juju leader.")
            return

        # checking if upgrade in progress
        if self.cluster_state != "idle":
            event.fail("Cannot run pre-upgrade checks, cluster already upgrading.")
            return

        try:
            logger.info("Running pre-upgrade-check...")
            self.pre_upgrade_check()

            if self.substrate == "k8s":
                logger.info("Building upgrade-stack for K8s...")
                built_upgrade_stack = sorted(
                    [int(unit.name.split("/")[1]) for unit in self.app_units]
                )
            else:
                logger.info("Building upgrade-stack for VMs...")
                built_upgrade_stack = self.build_upgrade_stack()

            logger.debug(f"Built upgrade stack of {built_upgrade_stack}")

        except ClusterNotReadyError as e:
            logger.error(e)
            event.fail(message=e.message)
            return
        except Exception as e:
            logger.error(e)
            event.fail(message="Unknown error found.")
            return

        logger.info("Setting upgrade-stack to relation data...")
        self.upgrade_stack = built_upgrade_stack

    def _upgrade_supported_check(self) -> None:
        """Checks if previous versions can be upgraded to new versions.

        Raises:
            :class:`VersionError` if upgrading to existing `version` is not supported
        """
        keys = self.dependency_model.__fields__.keys()

        compatible = True
        incompatibilities: List[Tuple[str, str, str, str]] = []
        for key in keys:
            old_dep: DependencyModel = getattr(self.stored_dependencies, key)
            new_dep: DependencyModel = getattr(self.dependency_model, key)

            if not old_dep.can_upgrade(dependency=new_dep):
                compatible = False
                incompatibilities.append(
                    (key, old_dep.version, new_dep.version, new_dep.upgrade_supported)
                )

        base_message = "Versions incompatible"
        base_cause = "Upgrades only supported for specific versions"
        if not compatible:
            for incompat in incompatibilities:
                base_message += (
                    f", {incompat[0]} {incompat[1]} can not be upgraded to {incompat[2]}"
                )
                base_cause += f", {incompat[0]} versions satisfying requirement {incompat[3]}"

            raise VersionError(
                message=base_message,
                cause=base_cause,
            )

    def _on_upgrade_charm(self, event: UpgradeCharmEvent) -> None:
        """Handler for `upgrade-charm` events."""
        # defer if not all units have pre-upgraded
        if not self.peer_relation:
            event.defer()
            return

        # if any other unit failed or if no stack (i.e pre-upgrade check), mark failed
        if not self.upgrade_stack or self.cluster_state == "failed":
            logger.error(
                "Cluster upgrade failed. Setting failed upgrade state... {}".format(
                    "Ensure pre-upgrade checks are ran first" if not self.upgrade_stack else ""
                )
            )
            self.set_unit_failed()
            self.log_rollback_instructions()
            return

        # run version checks on leader only
        if self.charm.unit.is_leader():
            try:
                self._upgrade_supported_check()
            except VersionError as e:  # not ready if not passed check
                logger.error(e)
                self.set_unit_failed()
                return

        # all units sets state to ready
        self.peer_relation.data[self.charm.unit].update(
            {"state": "ready" if self.substrate == "vm" else "upgrading"}
        )

    def on_upgrade_changed(self, event: EventBase) -> None:
        """Handler for `upgrade-relation-changed` events."""
        if not self.peer_relation:
            return

        # if any other unit failed, mark as failed
        if self.cluster_state == "failed":
            logger.error("Cluster upgrade failed. Setting failed upgrade state...")
            self.set_unit_failed()
            self.log_rollback_instructions()
            return

        # if all units completed, mark as complete
        if not self.upgrade_stack:
            if self.state == "completed" and self.cluster_state in ["idle", "completed"]:
                logger.info("All units completed upgrade, setting idle upgrade state...")
                self.peer_relation.data[self.charm.unit].update({"state": "idle"})
                return
            if self.cluster_state == "idle":
                logger.debug("upgrade-changed event handled before pre-checks, exiting...")
                return
            else:
                logger.debug("Did not find upgrade-stack or completed cluster state, deferring...")
                event.defer()
                return

        # pop mutates the `upgrade_stack` attr
        top_unit_id = self.upgrade_stack.pop()
        top_unit = self.charm.model.get_unit(f"{self.charm.app.name}/{top_unit_id}")
        top_state = self.peer_relation.data[top_unit].get("state")

        # if top of stack is completed, leader pops it
        if self.charm.unit.is_leader() and top_state == "completed":
            logger.debug(f"{top_unit} has finished upgrading, updating stack...")

            # writes the mutated attr back to rel data
            self.peer_relation.data[self.charm.app].update(
                {"upgrade-stack": json.dumps(self.upgrade_stack)}
            )

            # recurse on leader to ensure relation changed event not lost
            # in case leader is next or the last unit to complete
            self.on_upgrade_changed(event)

        # if unit top of stack, emit granted event
        if self.charm.unit == top_unit:
            if self.substrate == "vm" and top_state in ["ready", "upgrading"]:
                logger.debug(
                    f"{top_unit} is next to upgrade, emitting `upgrade_granted` event and upgrading..."
                )
                self.peer_relation.data[self.charm.unit].update({"state": "upgrading"})
                getattr(self.on, "upgrade_granted").emit()
            if self.substrate == "k8s" and top_state == "completed":
                logger.debug(
                    f"{top_unit} has completed the upgrade, emitting `upgrade_finished` event..."
                )
                getattr(self.on, "upgrade_finished").emit()

    def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
        """Handler for `upgrade-granted` events."""
        # don't raise if k8s substrate, only return
        if self.substrate == "k8s":
            return

        raise NotImplementedError

    def _on_upgrade_finished(self, event: UpgradeFinishedEvent) -> None:
        """Handler for `upgrade-finished` events."""
        # don't raise if vm substrate, only return
        if self.substrate == "vm":
            return

        raise NotImplementedError
