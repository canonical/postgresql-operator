# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrades implementation."""

import json
import logging
import os
import subprocess

from charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    DependencyModel,
    UpgradeGrantedEvent,
)
from charms.postgresql_k8s.v0.postgresql import ACCESS_GROUPS
from ops.model import MaintenanceStatus, RelationDataContent, WaitingStatus
from pydantic import BaseModel
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed
from typing_extensions import override

from constants import (
    APP_SCOPE,
    MONITORING_PASSWORD_KEY,
    MONITORING_USER,
    PATRONI_PASSWORD_KEY,
    PEER,
    RAFT_PASSWORD_KEY,
    SNAP_PACKAGES,
)
from utils import new_password

logger = logging.getLogger(__name__)


class PostgreSQLDependencyModel(BaseModel):
    """PostgreSQL dependencies model."""

    charm: DependencyModel
    snap: DependencyModel


def get_postgresql_dependencies_model() -> PostgreSQLDependencyModel:
    """Return the PostgreSQL dependencies model."""
    with open("src/dependency.json") as dependency_file:
        _deps = json.load(dependency_file)
    return PostgreSQLDependencyModel(**_deps)


class PostgreSQLUpgrade(DataUpgrade):
    """PostgreSQL upgrade class."""

    def __init__(self, charm, model: BaseModel, run_cmd: str, **kwargs) -> None:
        """Initialize the class."""
        super().__init__(charm, model, **kwargs)
        self.charm = charm
        self.run_cmd = run_cmd
        self._on_upgrade_charm_check_legacy()

    @override
    def build_upgrade_stack(self) -> list[int]:
        """Builds ordered iterable of all application unit.ids to upgrade in.

        Called by leader unit during :meth:`_on_pre_upgrade_check_action`.

        Returns:
            Iterable of integer unit.ids, LIFO ordered in upgrade order
                i.e `[5, 2, 4, 1, 3]`, unit `3` upgrades first, `5` upgrades last
        """
        primary_unit_id = int(
            self.charm._patroni.get_primary(unit_name_pattern=True).split("/")[1]
        )
        sync_standby_ids = [
            int(unit.split("/")[1]) for unit in self.charm._patroni.get_sync_standby_names()
        ]
        unit_ids = [int(self.charm.unit.name.split("/")[1])] + [
            int(unit.name.split("/")[1]) for unit in self.peer_relation.units
        ]
        # Sort the upgrade stack so replicas are upgraded first, then the sync-standbys
        # at the primary is the last unit to be upgraded.
        upgrade_stack = sorted(
            unit_ids,
            key=lambda x: 0 if x == primary_unit_id else 1 if x in sync_standby_ids else 2,
        )
        return upgrade_stack

    @override
    def log_rollback_instructions(self) -> None:
        """Log rollback instructions."""
        logger.info(
            "Run `juju refresh --revision <previous-revision> postgresql` to initiate the rollback"
        )

    def _on_upgrade_charm_check_legacy(self) -> None:
        if not self.peer_relation or len(self.app_units) < len(self.charm.app_units):
            logger.debug("Wait all units join the upgrade relation")
            return

        if self.charm.unit.is_leader() and self.charm._peers:
            for key in (
                RAFT_PASSWORD_KEY,
                PATRONI_PASSWORD_KEY,
            ):
                if self.charm.get_secret(APP_SCOPE, key) is None:
                    self.charm.set_secret(APP_SCOPE, key, new_password())

        if self.state:
            # If state set, upgrade is supported. Just set the snap information
            # in the dependencies, as it's missing in the first revisions that
            # support upgrades.
            dependencies = self.peer_relation.data[self.charm.app].get("dependencies")
            if (
                self.charm.unit.is_leader()
                and dependencies is not None
                and "snap" not in json.loads(dependencies)
            ):
                fixed_dependencies = json.loads(dependencies)
                fixed_dependencies["snap"] = {
                    "dependencies": {},
                    "name": "charmed-postgresql",
                    "upgrade_supported": "^14",
                    "version": "14.9",
                }
                self.peer_relation.data[self.charm.app].update({
                    "dependencies": json.dumps(fixed_dependencies)
                })
            return

        if not self.charm.unit.is_leader():
            # set ready state on non-leader units
            self.unit_upgrade_data.update({"state": "ready"})
            return

        peers_state = list(filter(lambda state: state != "", self.unit_states))

        if (
            len(peers_state) == len(self.peer_relation.units)
            and (set(peers_state) == {"ready"} or len(peers_state) == 0)
            and self.charm.is_cluster_initialised
        ):
            if self.charm._patroni.member_started:
                # All peers have set the state to ready
                self.unit_upgrade_data.update({"state": "ready"})
                self._prepare_upgrade_from_legacy()
            self.on.upgrade_charm.emit()

    @override
    def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
        # Refresh the charmed PostgreSQL snap and restart the database.
        # Update the configuration.
        self._remove_secrets_old_revisions()

        self.charm.unit.status = MaintenanceStatus("updating configuration")
        self.charm.update_config()
        self.charm.updated_synchronous_node_count()

        self.charm.unit.status = MaintenanceStatus("refreshing the snap")
        self.charm._install_snap_packages(packages=SNAP_PACKAGES, refresh=True)

        if not self.charm._patroni.start_patroni():
            logger.error("failed to start the database")
            self.set_unit_failed()
            return

        raft_encryption = (
            int(
                json.loads(self.peer_relation.data[self.charm.app].get("dependencies", "{}"))
                .get("charm", {})
                .get("version", 0)
            )
            < 3
        )
        self.charm._setup_exporter()
        self.charm.backup.start_stop_pgbackrest_service()

        try:
            self.charm.unit.set_workload_version(
                self.charm._patroni.get_postgresql_version() or "unset"
            )
        except TypeError:
            # Don't fail on this, just log it.
            logger.warning("Failed to get PostgreSQL version")

        # Wait until the database initialise.
        self.charm.unit.status = WaitingStatus("waiting for database initialisation")
        try:
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10)):
                with attempt:
                    # Check if the member hasn't started or hasn't joined the cluster yet.
                    if (
                        not self.charm._patroni.member_started
                        or self.charm.unit.name.replace("/", "-")
                        not in self.charm._patroni.cluster_members
                        or not self.charm._patroni.is_replication_healthy(raft_encryption)
                    ):
                        logger.debug(
                            "Instance not yet back in the cluster."
                            f" Retry {attempt.retry_state.attempt_number}/6"
                        )
                        raise Exception()

                    self.charm.update_config()

                    self._set_up_new_access_roles_for_legacy()

                    self.set_unit_completed()

                    # Ensures leader gets its own relation-changed when it upgrades
                    if self.charm.unit.is_leader():
                        self.on_upgrade_changed(event)
        except RetryError:
            logger.debug(
                "Defer on_upgrade_granted: member not ready or not joined the cluster yet"
            )
            event.defer()

    @override
    def pre_upgrade_check(self) -> None:
        """Runs necessary checks validating the cluster is in a healthy state to upgrade.

        Called by all units during :meth:`_on_pre_upgrade_check_action`.

        Raises:
            :class:`ClusterNotReadyError`: if cluster is not ready to upgrade
        """
        default_message = "Pre-upgrade check failed and cannot safely upgrade"
        if not self.charm._patroni.are_all_members_ready():
            raise ClusterNotReadyError(
                default_message,
                "not all members are ready yet",
                "wait for all units to become active/idle",
            )

        if self.charm._patroni.is_creating_backup:
            raise ClusterNotReadyError(
                default_message,
                "a backup is being created",
                "wait for the backup creation to finish before starting the upgrade",
            )

    def _prepare_upgrade_from_legacy(self) -> None:
        """Prepare upgrade from legacy charm without upgrade support.

        Assumes run on leader unit only.
        """
        logger.warning("Upgrading from unsupported version")

        # Populate app upgrade databag to allow upgrade procedure
        logger.debug("Building upgrade stack")
        upgrade_stack = self.build_upgrade_stack()
        logger.debug(f"Upgrade stack: {upgrade_stack}")
        self.upgrade_stack = upgrade_stack
        logger.debug("Persisting dependencies to upgrade relation data...")
        self.peer_relation.data[self.charm.app].update({
            "dependencies": json.dumps(self.dependency_model.dict())
        })
        if self.charm.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY) is None:
            self.charm.set_secret(APP_SCOPE, MONITORING_PASSWORD_KEY, new_password())
        users = self.charm.postgresql.list_users()
        if MONITORING_USER not in users:
            # Create the monitoring user.
            self.charm.postgresql.create_user(
                MONITORING_USER,
                self.charm.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
                extra_user_roles="pg_monitor",
            )
        self.charm.postgresql.set_up_database()

    def _remove_secrets_old_revisions(self) -> None:
        """Remove secrets' old revisions."""
        if self.charm.unit.is_leader():
            secret = self.charm.model.get_secret(label=f"{PEER}.{self.charm.app.name}.app")
            latest_revision = secret.get_info().revision
            # We need to trick Juju into thinking that we are not running in a hook context,
            # as Juju will disallow use of juju-run / juju-exec.
            new_env = os.environ.copy()
            if "JUJU_CONTEXT_ID" in new_env:
                new_env.pop("JUJU_CONTEXT_ID")
            for revision in range(1, latest_revision):
                if str(latest_revision).startswith(str(revision)):
                    # Skip if the revision is a prefix of the latest revision.
                    logger.info(
                        f"Skipping secret revision {revision} because it's the prefix of the latest revision (see https://github.com/juju/juju/issues/20782)"
                    )
                    continue
                command = [
                    self.run_cmd,
                    self.charm.unit.name,
                    "--",
                    "secret-remove",
                    "--revision",
                    str(revision),
                    secret.get_info().id,
                ]
                # Input comes from the charm.
                subprocess.Popen(command, env=new_env)  # noqa: S603
                logger.info(f"Removing secret revision {revision}")

    def _set_up_new_access_roles_for_legacy(self) -> None:
        """Create missing access groups and their memberships."""
        access_groups = self.charm.postgresql.list_access_groups()
        if access_groups == set(ACCESS_GROUPS) and sorted(
            self.charm.postgresql.list_users_from_relation()
        ) == sorted(self.charm.postgresql.list_users(group="relation_access")):
            return

        self.charm.postgresql.create_access_groups()
        self.charm.postgresql.grant_internal_access_group_memberships()
        self.charm.postgresql.grant_relation_access_group_memberships()
        logger.debug("Access roles created")

    @property
    def unit_upgrade_data(self) -> RelationDataContent:
        """Return the application upgrade data."""
        return self.peer_relation.data[self.charm.unit]
