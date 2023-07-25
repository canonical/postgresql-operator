# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrades implementation."""
import json
import logging
from typing import List

from charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    DependencyModel,
    UpgradeGrantedEvent,
)
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus
from pydantic import BaseModel
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed
from typing_extensions import override

from constants import SNAP_PACKAGES

logger = logging.getLogger(__name__)


class PostgreSQLDependencyModel(BaseModel):
    """PostgreSQL dependencies model."""

    charm: DependencyModel


def get_postgresql_dependencies_model() -> PostgreSQLDependencyModel:
    """Return the PostgreSQL dependencies model."""
    with open("src/dependency.json") as dependency_file:
        _deps = json.load(dependency_file)
    return PostgreSQLDependencyModel(**_deps)


class PostgreSQLUpgrade(DataUpgrade):
    """PostgreSQL upgrade class."""

    def __init__(self, charm, model: BaseModel, **kwargs) -> None:
        """Initialize the class."""
        super().__init__(charm, model, **kwargs)
        self.charm = charm

    @override
    def build_upgrade_stack(self) -> List[int]:
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

    @override
    def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
        # Refresh the charmed PostgreSQL snap and restart the database.
        self.charm.unit.status = MaintenanceStatus("refreshing the snap")
        self.charm._install_snap_packages(packages=SNAP_PACKAGES, refresh=True)

        if not self.charm._patroni.start_patroni():
            logger.error("failed to start the database")
            self.set_unit_failed()
            return

        self.charm._setup_exporter()
        self.charm.backup.start_stop_pgbackrest_service()

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
                    ):
                        logger.debug(
                            "Instance not yet back in the cluster."
                            f" Retry {attempt.retry_state.attempt_number}/6"
                        )
                        raise Exception()

                    self.set_unit_completed()
                    self.charm.unit.status = ActiveStatus()

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
