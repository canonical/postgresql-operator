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

r"""Library to manage in-place upgrades for charms running on VMs and K8s.

This library contains handlers for `upgrade` relation events used to coordinate
between units in an application during a `juju refresh`, as well as `Pydantic` models
for instantiating, validating and comparing dependencies.

An upgrade on VMs is initiated with the command `juju refresh`. Once executed, the following
events are emitted to each unit at random:
    - `upgrade-charm`
    - `config-changed`
    - `leader-settings-changed` - Non-leader only

Charm authors can implement the classes defined in this library to streamline the process of
coordinating which unit updates when, achieved through updating of unit-data `state` throughout.

At a high-level, the upgrade steps are as follows:
    - Run pre-checks on the cluster to confirm it is safe to upgrade
    - Create stack of unit.ids, to serve as the upgrade order (generally workload leader is last)
    - Start the upgrade by issuing a Juju CLI command
    - The unit at the top of the stack gets permission to upgrade
    - The unit handles the upgrade and restarts their service
    - Repeat, until all units have restarted

### Usage by charm authors

#### `upgrade` relation

Charm authors must implement an additional peer-relation.

As this library uses relation data exchanged between units to coordinate, charm authors
need to add a new relation interface. The relation name does not matter.

`metadata.yaml`
```yaml
peers:
  upgrade:
    interface: upgrade
```

#### Dependencies JSON/Dict

Charm authors must implement a dict object tracking current charm versions, requirements + upgradability.

Many workload versions may be incompatible with older/newer versions. This same idea also can apply to
charm or snap versions. Workloads with required related applications (e.g Kafka + ZooKeeper) also need to
ensure their versions are compatible during an upgrade, to avoid cluster failure.

As such, it is necessasry to freeze any dependencies within each published charm. An example of this could
be creating a `DEPENDENCIES` dict within the charm code, with the following structure:

`src/literals.py`
```python
DEPENDENCIES = {
    "kafka_charm": {
        "dependencies": {"zookeeper": ">50"},
        "name": "kafka",
        "upgrade_supported": ">90",
        "version": "100",
    },
    "kafka_service": {
        "dependencies": {"zookeeper": "^3"},
        "name": "kafka",
        "upgrade_supported": ">=0.8",
        "version": "3.3.2",
    },
}
```

The first-level key names are arbitrary labels for tracking what those versions+dependencies are for.
The `dependencies` second-level values are a key-value map of any required external applications,
    and the versions this packaged charm can support.
The `upgrade_suppported` second-level values are requirements from which an in-place upgrade can be
    supported by the charm.
The `version` second-level values correspond to the current version of this packaged charm.

Any requirements comply with [`poetry`'s dependency specifications](https://python-poetry.org/docs/dependency-specification/#caret-requirements).

### Dependency Model

Charm authors must implement their own class inheriting from `DependencyModel`.

Using a `Pydantic` model to instantiate the aforementioned `DEPENDENCIES` dict gives stronger type safety and additional
layers of validation.

Implementation just needs to ensure that the top-level key names from `DEPENDENCIES` are defined as attributed in the model.

`src/upgrade.py`
```python
from pydantic import BaseModel

class KafkaDependenciesModel(BaseModel):
    kafka_charm: DependencyModel
    kafka_service: DependencyModel
```

### Overrides for `DataUpgrade`

Charm authors must define their own class, inheriting from `DataUpgrade`, overriding all required `abstractmethod`s.

```python
class ZooKeeperUpgrade(DataUpgrade):
    def __init__(self, charm: "ZooKeeperUpgrade", **kwargs):
        super().__init__(charm, **kwargs)
        self.charm = charm
```

#### Implementation of `pre_upgrade_check()`

Before upgrading a cluster, it's a good idea to check that it is stable and healthy before permitting it.
Here, charm authors can validate upgrade safety through API calls, relation-data checks, etc.
If any of these checks fail, raise `ClusterNotReadyError`.

```python
    @override
    def pre_upgrade_check(self) -> None:
        default_message = "Pre-upgrade check failed and cannot safely upgrade"
        try:
            if not self.client.members_broadcasting or not len(self.client.server_members) == len(
                self.charm.cluster.peer_units
            ):
                raise ClusterNotReadyError(
                    message=default_message,
                    cause="Not all application units are connected and broadcasting in the quorum",
                )

            if self.client.members_syncing:
                raise ClusterNotReadyError(
                    message=default_message, cause="Some quorum members are syncing data"
                )

            if not self.charm.cluster.stable:
                raise ClusterNotReadyError(
                    message=default_message, cause="Charm has not finished initialising"
                )

        except QuorumLeaderNotFoundError:
            raise ClusterNotReadyError(message=default_message, cause="Quorum leader not found")
        except ConnectionClosedError:
            raise ClusterNotReadyError(
                message=default_message, cause="Unable to connect to the cluster"
            )
```

#### Implementation of `build_upgrade_stack()` - VM ONLY

Oftentimes, it is necessary to ensure that the workload leader is the last unit to upgrade,
to ensure high-availability during the upgrade process.
Here, charm authors can create a LIFO stack of unit.ids, represented as a list of unit.id strings,
with the leader unit being at i[0].

```python
@override
def build_upgrade_stack(self) -> list[int]:
    upgrade_stack = []
    for unit in self.charm.cluster.peer_units:
        config = self.charm.cluster.unit_config(unit=unit)

        # upgrade quorum leader last
        if config["host"] == self.client.leader:
            upgrade_stack.insert(0, int(config["unit_id"]))
        else:
            upgrade_stack.append(int(config["unit_id"]))

    return upgrade_stack
```

#### Implementation of `_on_upgrade_granted()`

On relation-changed events, each unit will check the current upgrade-stack persisted to relation data.
If that unit is at the top of the stack, it will emit an `upgrade-granted` event, which must be handled.
Here, workloads can be re-installed with new versions, checks can be made, data synced etc.
If the new unit successfully rejoined the cluster, call `set_unit_completed()`.
If the new unit failed to rejoin the cluster, call `set_unit_failed()`.

NOTE - It is essential here to manually call `on_upgrade_changed` if the unit is the current leader.
This ensures that the leader gets it's own relation-changed event, and updates the upgrade-stack for
other units to follow suit.

```python
@override
def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
    self.charm.snap.stop_snap_service()

    if not self.charm.snap.install():
        logger.error("Unable to install ZooKeeper Snap")
        self.set_unit_failed()
        return None

    logger.info(f"{self.charm.unit.name} upgrading service...")
    self.charm.snap.restart_snap_service()

    try:
        logger.debug("Running post-upgrade check...")
        self.pre_upgrade_check()

        logger.debug("Marking unit completed...")
        self.set_unit_completed()

        # ensures leader gets it's own relation-changed when it upgrades
        if self.charm.unit.is_leader():
            logger.debug("Re-emitting upgrade-changed on leader...")
            self.on_upgrade_changed(event)

    except ClusterNotReadyError as e:
        logger.error(e.cause)
        self.set_unit_failed()
```

#### Implementation of `log_rollback_instructions()`

If the upgrade fails, manual intervention may be required for cluster recovery.
Here, charm authors can log out any necessary steps to take to recover from a failed upgrade.
When a unit fails, this library will automatically log out this message.

```python
@override
def log_rollback_instructions(self) -> None:
    logger.error("Upgrade failed. Please run `juju refresh` to previous version.")
```

### Instantiating in the charm and deferring events

Charm authors must add a class attribute for the child class of `DataUpgrade` in the main charm.
They must also ensure that any non-upgrade related events that may be unsafe to handle during
an upgrade, are deferred if the unit is not in the `idle` state - i.e not currently upgrading.

```python
class ZooKeeperCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.upgrade = ZooKeeperUpgrade(
            self,
            relation_name = "upgrade",
            substrate = "vm",
            dependency_model=ZooKeeperDependencyModel(
                **DEPENDENCIES
            ),
        )

    def restart(self, event) -> None:
        if not self.upgrade.state == "idle":
            event.defer()
            return None

        self.restart_snap_service()
```
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Literal, Optional, Set, Tuple

import poetry.core.constraints.version as poetry_version
from ops.charm import (
    ActionEvent,
    CharmBase,
    CharmEvents,
    RelationCreatedEvent,
    UpgradeCharmEvent,
)
from ops.framework import EventBase, EventSource, Object
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, Relation, Unit, WaitingStatus
from pydantic import BaseModel, root_validator, validator

# The unique Charmhub library identifier, never change it
LIBID = "156258aefb79435a93d933409a8c8684"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 18

PYDEPS = ["pydantic>=1.10,<2", "poetry-core"]

logger = logging.getLogger(__name__)

# --- DEPENDENCY RESOLUTION FUNCTIONS ---


def verify_requirements(version: str, requirement: str) -> bool:
    """Verifies a specified version against defined constraint.

    Supports Poetry version constraints
    https://python-poetry.org/docs/dependency-specification/#version-constraints

    Args:
        version: the version currently in use
        requirement: Poetry version constraint

    Returns:
        True if `version` meets defined `requirement`. Otherwise False
    """
    return poetry_version.parse_constraint(requirement).allows(
        poetry_version.Version.parse(version)
    )


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

    dependencies: Dict[str, str]
    name: str
    upgrade_supported: str
    version: str

    @validator("dependencies", "upgrade_supported", each_item=True)
    @classmethod
    def dependencies_validator(cls, value):
        """Validates version constraint."""
        if isinstance(value, dict):
            deps = value.values()
        else:
            deps = [value]

        for dep in deps:
            poetry_version.parse_constraint(dep)

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


class KubernetesClientError(UpgradeError):
    """Exception flagging that a call to Kubernetes API failed.

    For example, if the cluster fails :class:`DataUpgrade._set_rolling_update_partition`

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
    """Used to tell units that they can process an upgrade."""


class UpgradeFinishedEvent(EventBase):
    """Used to tell units that they finished the upgrade."""


class UpgradeEvents(CharmEvents):
    """Upgrade events.

    This class defines the events that the lib can emit.
    """

    upgrade_granted = EventSource(UpgradeGrantedEvent)
    upgrade_finished = EventSource(UpgradeFinishedEvent)


# --- EVENT HANDLER ---


class DataUpgrade(Object, ABC):
    """Manages `upgrade` relation operations for in-place upgrades."""

    STATES = ["recovery", "failed", "idle", "ready", "upgrading", "completed"]

    on = UpgradeEvents()  # pyright: ignore [reportAssignmentType]

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
        if self.substrate == "k8s":
            self.framework.observe(
                getattr(self.charm.on, "resume_upgrade_action"), self._on_resume_upgrade_action
            )

    @property
    def peer_relation(self) -> Optional[Relation]:
        """The upgrade peer relation."""
        return self.charm.model.get_relation(self.relation_name)

    @property
    def app_units(self) -> Set[Unit]:
        """The peer-related units in the application."""
        if not self.peer_relation:
            return set()

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
    def other_unit_states(self) -> list:
        """Current upgrade state for other units.

        Returns:
            Unsorted list of upgrade states for other units.
        """
        if not self.peer_relation:
            return []

        return [
            self.peer_relation.data[unit].get("state", "")
            for unit in list(self.peer_relation.units)
        ]

    @property
    def unit_states(self) -> list:
        """Current upgrade state for all units.

        Returns:
            Unsorted list of upgrade states for all units.
        """
        if not self.peer_relation:
            return []

        return [self.peer_relation.data[unit].get("state", "") for unit in self.app_units]

    @property
    def cluster_state(self) -> Optional[str]:
        """Current upgrade state for cluster units.

        Determined from :class:`DataUpgrade.STATE`, taking the lowest ordinal unit state.

        For example, if units in have states: `["ready", "upgrading", "completed"]`,
            the overall state for the cluster is `ready`.

        Returns:
            String of upgrade state from the furthest behind unit.
        """
        if not self.unit_states:
            return None

        try:
            return sorted(self.unit_states, key=self.STATES.index)[0]
        except (ValueError, KeyError):
            return None

    @property
    def idle(self) -> Optional[bool]:
        """Flag for whether the cluster is in an idle upgrade state.

        Returns:
            True if all application units in idle state. Otherwise False
        """
        return set(self.unit_states) == {"idle"}

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

    def _repair_upgrade_stack(self) -> None:
        """Ensures completed units are re-added to the upgrade-stack after failure."""
        # need to update the stack as it was not refreshed by rollback run of pre-upgrade-check
        # avoids difficult health check implementation by charm-authors needing to exclude dead units

        # if the first unit in the stack fails, the stack will be the same length as units
        # i.e this block not ran
        if (
            self.cluster_state in ["failed", "recovery"]
            and self.upgrade_stack
            and len(self.upgrade_stack) != len(self.app_units)
            and self.charm.unit.is_leader()
        ):
            new_stack = self.upgrade_stack
            for unit in self.app_units:
                unit_id = int(unit.name.split("/")[1])

                # if a unit fails, it rolls back first
                if unit_id not in new_stack:
                    new_stack.insert(-1, unit_id)
                    logger.debug(f"Inserted {unit_id} in to upgrade-stack - {new_stack}")

            self.upgrade_stack = new_stack

    def set_unit_failed(self, cause: Optional[str] = None) -> None:
        """Sets unit `state=failed` to the upgrade peer data.

        Args:
            cause: short description of cause of failure
        """
        if not self.peer_relation:
            return None

        # needed to refresh the stack
        # now leader pulls a fresh stack from newly updated relation data
        if self.charm.unit.is_leader():
            self._upgrade_stack = None

        self.charm.unit.status = BlockedStatus(cause if cause else "")
        self.peer_relation.data[self.charm.unit].update({"state": "failed"})
        self.log_rollback_instructions()

    def set_unit_completed(self) -> None:
        """Sets unit `state=completed` to the upgrade peer data."""
        if not self.peer_relation:
            return None

        # needed to refresh the stack
        # now leader pulls a fresh stack from newly updated relation data
        if self.charm.unit.is_leader():
            self._upgrade_stack = None

        self.charm.unit.status = MaintenanceStatus("upgrade completed")
        self.peer_relation.data[self.charm.unit].update({"state": "completed"})

        # Emit upgrade_finished event to run unit's post upgrade operations.
        if self.substrate == "k8s":
            logger.debug(
                f"{self.charm.unit.name} has completed the upgrade, emitting `upgrade_finished` event..."
            )
            getattr(self.on, "upgrade_finished").emit()

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

        if self.cluster_state == "failed":
            logger.info("Entering recovery state for rolling-back to previous version...")
            self._repair_upgrade_stack()
            self.charm.unit.status = BlockedStatus("ready to rollback application")
            self.peer_relation.data[self.charm.unit].update({"state": "recovery"})
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

    def _on_resume_upgrade_action(self, event: ActionEvent) -> None:
        """Handle resume upgrade action.

        Continue the upgrade by setting the partition to the next unit.
        """
        if not self.peer_relation:
            event.fail(message="Could not find upgrade relation.")
            return

        if not self.charm.unit.is_leader():
            event.fail(message="Action must be ran on the Juju leader.")
            return

        if not self.upgrade_stack:
            event.fail(message="Nothing to resume, upgrade stack unset.")
            return

        # Check whether this is being run after juju refresh was called
        # (the size of the upgrade stack should match the number of total
        # unit minus one).
        if len(self.upgrade_stack) != len(self.peer_relation.units):
            event.fail(message="Upgrade can be resumed only once after juju refresh is called.")
            return

        try:
            next_partition = self.upgrade_stack[-1]
            self._set_rolling_update_partition(partition=next_partition)
            event.set_results({"message": f"Upgrade will resume on unit {next_partition}"})
        except KubernetesClientError:
            event.fail(message="Cannot set rolling update partition.")

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

        if not self.upgrade_stack:
            logger.error("Cluster upgrade failed, ensure pre-upgrade checks are ran first.")
            return

        if self.substrate == "vm":
            # for VM run version checks on leader only
            if self.charm.unit.is_leader():
                try:
                    self._upgrade_supported_check()
                except VersionError as e:  # not ready if not passed check
                    logger.error(e)
                    self.set_unit_failed()
                    return
                top_unit_id = self.upgrade_stack[-1]
                top_unit = self.charm.model.get_unit(f"{self.charm.app.name}/{top_unit_id}")
                if (
                    top_unit == self.charm.unit
                    and self.peer_relation.data[self.charm.unit].get("state") == "recovery"
                ):
                    # While in a rollback and the Juju leader unit is the top unit in the upgrade stack, emit the event
                    # for this unit to start the rollback.
                    self.peer_relation.data[self.charm.unit].update({"state": "ready"})
                    self.on_upgrade_changed(event)
                    return
            self.charm.unit.status = WaitingStatus("other units upgrading first...")
            self.peer_relation.data[self.charm.unit].update({"state": "ready"})

            if len(self.app_units) == 1:
                # single unit upgrade, emit upgrade_granted event right away
                getattr(self.on, "upgrade_granted").emit()

        else:
            # for k8s run version checks only on highest ordinal unit
            if (
                self.charm.unit.name
                == f"{self.charm.app.name}/{self.charm.app.planned_units() -1}"
            ):
                try:
                    self._upgrade_supported_check()
                except VersionError as e:  # not ready if not passed check
                    logger.error(e)
                    self.set_unit_failed()
                    return
            # On K8s an unit that receives the upgrade-charm event is upgrading
            self.charm.unit.status = MaintenanceStatus("upgrading unit")
            self.peer_relation.data[self.charm.unit].update({"state": "upgrading"})

    def on_upgrade_changed(self, event: EventBase) -> None:
        """Handler for `upgrade-relation-changed` events."""
        if not self.peer_relation:
            return

        # if any other unit failed, don't continue with upgrade
        if self.cluster_state == "failed":
            logger.debug("Cluster failed to upgrade, exiting...")
            return

        if self.substrate == "vm" and self.cluster_state == "recovery":
            # skip run while in recovery. The event will be retrigged when the cluster is ready
            logger.debug("Cluster in recovery, skip...")
            return

        # if all units completed, mark as complete
        if not self.upgrade_stack:
            if self.state == "completed" and self.cluster_state in ["idle", "completed"]:
                logger.info("All units completed upgrade, setting idle upgrade state...")
                self.charm.unit.status = ActiveStatus()
                self.peer_relation.data[self.charm.unit].update({"state": "idle"})

                if self.charm.unit.is_leader():
                    logger.debug("Persisting new dependencies to upgrade relation data...")
                    self.peer_relation.data[self.charm.app].update(
                        {"dependencies": json.dumps(self.dependency_model.dict())}
                    )
                return

            if self.cluster_state == "idle":
                logger.debug("upgrade-changed event handled before pre-checks, exiting...")
                return

            logger.debug("Did not find upgrade-stack or completed cluster state, skipping...")
            return

        # upgrade ongoing, set status for waiting units
        if "upgrading" in self.unit_states and self.state in ["idle", "ready"]:
            self.charm.unit.status = WaitingStatus("other units upgrading first...")

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

        # if unit top of stack and all units ready (i.e stack), emit granted event
        if (
            self.charm.unit == top_unit
            and top_state in ["ready", "upgrading"]
            and self.cluster_state == "ready"
            and "upgrading" not in self.other_unit_states
        ):
            logger.debug(
                f"{top_unit.name} is next to upgrade, emitting `upgrade_granted` event and upgrading..."
            )
            self.charm.unit.status = MaintenanceStatus("upgrading...")
            self.peer_relation.data[self.charm.unit].update({"state": "upgrading"})

            try:
                getattr(self.on, "upgrade_granted").emit()
            except DependencyError as e:
                logger.error(e)
                self.set_unit_failed()
                return

    def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
        """Handler for `upgrade-granted` events.

        Handlers of this event must meet the following:
            - SHOULD check for related application deps from :class:`DataUpgrade.dependencies`
                - MAY raise :class:`DependencyError` if dependency not met
            - MUST update unit `state` after validating the success of the upgrade, calling one of:
                - :class:`DataUpgrade.set_unit_failed` if the unit upgrade fails
                - :class:`DataUpgrade.set_unit_completed` if the unit upgrade succeeds
            - MUST call :class:`DataUpgarde.on_upgrade_changed` on exit so event not lost on leader
        """
        # don't raise if k8s substrate, only return
        if self.substrate == "k8s":
            return

        raise NotImplementedError

    def _on_upgrade_finished(self, _) -> None:
        """Handler for `upgrade-finished` events."""
        if self.substrate == "vm" or not self.peer_relation:
            return

        # Emit the upgrade relation changed event in the leader to update the upgrade_stack.
        if self.charm.unit.is_leader():
            self.charm.on[self.relation_name].relation_changed.emit(
                self.model.get_relation(self.relation_name)
            )

        # This hook shouldn't run for the last unit (the first that is upgraded). For that unit it
        # should be done through an action after the upgrade success on that unit is double-checked.
        unit_number = int(self.charm.unit.name.split("/")[1])
        if unit_number == len(self.peer_relation.units):
            logger.info(
                f"{self.charm.unit.name} unit upgraded. Evaluate and run `resume-upgrade` action to continue upgrade"
            )
            return

        # Also, the hook shouldn't run for the first unit (the last that is upgraded).
        if unit_number == 0:
            logger.info(f"{self.charm.unit.name} unit upgraded. Upgrade is complete")
            return

        try:
            # Use the unit number instead of the upgrade stack to avoid race conditions
            # (i.e. the leader updates the upgrade stack after this hook runs).
            next_partition = unit_number - 1
            logger.debug(f"Set rolling update partition to unit {next_partition}")
            self._set_rolling_update_partition(partition=next_partition)
        except KubernetesClientError:
            logger.exception("Cannot set rolling update partition")
            self.set_unit_failed()
            self.log_rollback_instructions()

    def _set_rolling_update_partition(self, partition: int) -> None:
        """Patch the StatefulSet's `spec.updateStrategy.rollingUpdate.partition`.

        Args:
            partition: partition to set.

        K8s only. It should decrement the rolling update strategy partition by using a code
        like the following:

            from lightkube.core.client import Client
            from lightkube.core.exceptions import ApiError
            from lightkube.resources.apps_v1 import StatefulSet

            try:
                patch = {"spec": {"updateStrategy": {"rollingUpdate": {"partition": partition}}}}
                Client().patch(StatefulSet, name=self.charm.model.app.name, namespace=self.charm.model.name, obj=patch)
                logger.debug(f"Kubernetes StatefulSet partition set to {partition}")
            except ApiError as e:
                if e.status.code == 403:
                    cause = "`juju trust` needed"
                else:
                    cause = str(e)
                raise KubernetesClientError("Kubernetes StatefulSet patch failed", cause)
        """
        if self.substrate == "vm":
            return

        raise NotImplementedError
