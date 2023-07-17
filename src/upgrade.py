# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrades implementation."""
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
from tenacity import RetryError, Retrying, stop_after_attempt, wait_exponential
from typing_extensions import override

from constants import SNAP_PACKAGES

logger = logging.getLogger(__name__)


class PostgreSQLDependencyModel(BaseModel):
    """PostgreSQL dependencies model."""

    charm: DependencyModel


class PostgreSQLUpgrade(DataUpgrade):
    """PostgreSQL upgrade class."""

    @override
    def build_upgrade_stack(self) -> List[int]:
        """Builds ordered iterable of all application unit.ids to upgrade in.

        Called by leader unit during :meth:`_on_pre_upgrade_check_action`.

        Returns:
            Iterable of integer unit.ids, LIFO ordered in upgrade order
                i.e `[5, 2, 4, 1, 3]`, unit `3` upgrades first, `5` upgrades last
        """
        primary_unit_id = self.charm._patroni.get_primary(unit_name_pattern=True).split("/")[1]
        sync_standby_ids = [
            unit.split("/")[1] for unit in self.charm._patroni.get_sync_standby_names()
        ]
        unit_ids = [self.charm.unit.name.split("/")[1]] + [
            unit.name.split("/")[1] for unit in self.charm._peers.units
        ]
        upgrade_stack = sorted(
            unit_ids,
            key=lambda x: 0 if x == primary_unit_id else 1 if x in sync_standby_ids else 2,
        )
        logger.error(f"upgrade_stack: {upgrade_stack}")
        return upgrade_stack

    @override
    def log_rollback_instructions(self) -> None:
        """Log rollback instructions."""
        logger.info("Run `juju refresh --revision <previous-revision> postgresql` to rollback")

    @override
    def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
        # Refresh the charmed PostgreSQL snap and restart the database.
        logger.error("refreshing the snap")
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
            for attempt in Retrying(
                stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
            ):
                with attempt:
                    if self.charm._patroni.member_started:
                        self.charm.unit.status = ActiveStatus()
                    else:
                        raise Exception()
        except RetryError:
            logger.error("Defer on_upgrade_granted: member not ready yet")
            event.defer()
            return

        try:
            self.pre_upgrade_check()
            self.set_unit_completed()

            # ensures leader gets its own relation-changed when it upgrades
            if self.charm.unit.is_leader():
                self.on_upgrade_changed(event)

        except ClusterNotReadyError as e:
            logger.error(e.cause)
            self.set_unit_failed()

    @override
    def pre_upgrade_check(self) -> None:
        """Runs necessary checks validating the cluster is in a healthy state to upgrade.

        Called by all units during :meth:`_on_pre_upgrade_check_action`.

        Raises:
            :class:`ClusterNotReadyError`: if cluster is not ready to upgrade
        """
        if not self.charm.is_cluster_initialised:
            message = "cluster has not initialised yet"
            raise ClusterNotReadyError(message, message)

        # check for backups running.

        # check for tools in relation, like pgbouncer, being upgraded first?
