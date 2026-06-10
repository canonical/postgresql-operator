#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General PostgreSQL charm events."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, cast

from ops import (
    InstallEvent,
    LeaderElectedEvent,
    ModelError,
    Object,
    StartEvent,
    WaitingStatus,
    WorkloadEvent,
)
from tenacity import Retrying, stop_after_attempt, wait_fixed

from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.exceptions import (
    SettingSystemPasswordError,
    StorageUnavailableError,
)
from single_kernel_postgresql.config.statuses import GeneralStatuses, PatroniStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.managers.cluster import ClusterManager
from single_kernel_postgresql.managers.config import ConfigManager
from single_kernel_postgresql.managers.patroni import PatroniManager
from single_kernel_postgresql.managers.tls import TLSManager
from single_kernel_postgresql.workload.base import BaseWorkload
from single_kernel_postgresql.workload.vm import VMWorkload

if TYPE_CHECKING:
    from single_kernel_postgresql.charms.abstract_charm import AbstractPostgreSQLCharm
    from single_kernel_postgresql.charms.k8s_charm import PostgreSQLK8sCharm

logger = logging.getLogger(__name__)


class PostgreSQLEventsHandler(Object):
    """Class implementing PostgreSQL Charm events handling."""

    def __init__(
        self,
        charm: "AbstractPostgreSQLCharm",
        workload: BaseWorkload,
        state: CharmState,
        cluster_manager: ClusterManager,
        tls_manager: TLSManager,
        config_manager: ConfigManager,
        patroni_manager: PatroniManager,
    ) -> None:
        super().__init__(charm, key="postgresql_events")
        self.charm = charm
        self.workload = workload
        self.state = state
        self.cluster_manager = cluster_manager
        self.config_manager = config_manager
        self.tls_manager = tls_manager
        self.patroni_manager = patroni_manager

        # Charm events
        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
        self.framework.observe(self.charm.on.leader_elected, self._on_leader_elected)
        if self.state.substrate == Substrates.K8S:
            self.framework.observe(
                self.charm.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready
            )

    def _on_install(self, event: InstallEvent) -> None:
        """Install prerequisites for the application."""
        logger.debug("Install start time: %s", datetime.now())
        if self.charm.substrate == Substrates.VM and isinstance(self.workload, VMWorkload):
            self._check_detached_storage(self.workload)

        self.state.add_status_if_not_present(
            GeneralStatuses.MAINTAINENANCE_INSTALLING.value,
            scope="unit",
            component=self.cluster_manager.name,
        )
        # Install the charmed PostgreSQL snap.
        self.cluster_manager.install_workload()

        self.state.remove_status_if_present(
            GeneralStatuses.MAINTAINENANCE_INSTALLING.value,
            scope="unit",
            component=self.cluster_manager.name,
        )
        self.state.add_status_if_not_present(
            GeneralStatuses.WAITING_POSTGRESQL_START.value,
            scope="unit",
            component=self.cluster_manager.name,
        )

    def _on_start(self, event: StartEvent) -> None:
        """Event handler for start event."""
        if not self._can_start(event):
            return

        try:
            postgres_password = self.state.application.user_password
        except ModelError:
            logger.debug("_on_start: secrets not yet available")
            postgres_password = None
        # If the leader was not elected (and the needed passwords were not generated yet),
        # the cluster cannot be bootstrapped yet.
        if not postgres_password or not self.state.application.replication_password:
            logger.info("leader not elected and/or passwords not yet generated")
            event.defer()
            return

        if not self.state.application.internal_ca:
            logger.info("leader not elected and/or internal CA not yet generated")
            event.defer()
            return
        self.tls_manager.configure_internal_peer_cert()

        self.cluster_manager.expose_ip_and_port()

        self._start_primary(event)

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        charm = cast("PostgreSQLK8sCharm", self.charm)
        # TODO: Safeguard against refresh
        if self.state.endpoint in self.state.endpoints:
            # TODO: Fix pod by adding services
            pass

        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload
        if not container.can_connect():
            logger.debug(
                "Defer on_postgresql_pebble_ready: Waiting for container to become available"
            )
            event.defer()
            return
        # Create the PostgreSQL data directory. This is needed on cloud environments
        # where the volume is mounted with more restrictive permissions.
        # TODO: Create pgdata

        if not self.state.application.internal_ca:
            logger.info("leader not elected and/or internal CA not yet generated")
            event.defer()
            return
        if not self.state.peer.internal_cert:
            self.tls_manager.configure_internal_peer_cert()

        # Start the database service
        charm.k8s_manager.update_pebble_layers()

        # Assert the member is up and running before marking it as initialised.
        if not self.patroni_manager.member_started:
            logger.debug("Deferring on_start: awaiting for member to start")
            event.defer()
            return

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Event handler for leader elected event."""
        try:
            self.cluster_manager.configure_system_passwords()
        except SettingSystemPasswordError:
            self.state.add_status_if_not_present(
                GeneralStatuses.FAILED_SETTING_PASSWORDS.value,
                scope="unit",
                component=self.cluster_manager.name,
            )
            event.defer()

        # TODO: Check raft keys and initialize

        self.tls_manager.configure_internal_peer_ca()

        # TODO: Add next steps of leader elected

    def _can_start(self, event: StartEvent) -> bool:
        """Returns whether the workload can be started on this unit."""
        if self.charm.substrate == Substrates.VM and isinstance(self.workload, VMWorkload):
            self._check_detached_storage(self.workload)

        # Safeguard against starting while refreshing.
        # TODO: Add refresh checks once refresh is refactored

        # Doesn't try to bootstrap the cluster if it's in a blocked state
        # caused, for example, because a failed installation of packages.
        if self.state.peer.is_blocked:
            logger.debug("Early exit on_start: Unit blocked")
            return False

        return True

    def _start_primary(self, event: StartEvent) -> None:
        """Bootstrap the cluster."""
        # Set some information needed by Patroni to bootstrap the cluster.
        self.config_manager.configure_patroni_on_unit()

        if not self.patroni_manager.start_patroni():
            self.state.add_status_if_not_present(
                PatroniStatuses.FAILLED_STARTING_PATRONI.value,
                scope="unit",
                component=self.patroni_manager.name,
            )
            return

        # Assert the member is up and running before marking it as initialised.
        if not self.patroni_manager.member_started:
            logger.debug("Deferring on_start: awaiting for member to start")
            event.defer()
            return

        if not self.cluster_manager.can_connect_to_postgresql:
            logger.debug("Deferring on_start: awaiting for database to start")
            event.defer()
            return

        # TODO: Check primary endpoint

    def _check_detached_storage(self, workload: VMWorkload) -> None:
        """Wait for storage to become available.

        Workaround for lxd containers not getting storage attached on startups.
        """
        cached_status = self.charm.unit.status
        for attempt in Retrying(stop=stop_after_attempt(10), wait=wait_fixed(1), reraise=True):
            with attempt:
                if not workload.is_storage_attached():
                    logger.error("Data directory not attached.")
                    self.charm.unit.status = WaitingStatus("Data directory not attached")
                    raise StorageUnavailableError()
        self.charm.unit.status = cached_status
