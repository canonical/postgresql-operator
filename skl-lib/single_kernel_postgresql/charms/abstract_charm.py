# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Skeleton for the abstract charm."""

from abc import ABC, abstractmethod

from data_platform_helpers.advanced_statuses import StatusHandler
from ops.charm import CharmBase

from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.events.postgresql import PostgreSQLEventsHandler
from single_kernel_postgresql.managers.cluster import ClusterManager
from single_kernel_postgresql.managers.config import ConfigManager
from single_kernel_postgresql.managers.patroni import PatroniManager
from single_kernel_postgresql.managers.tls import TLSManager
from single_kernel_postgresql.workload.base import BaseWorkload

from ..config.enums import Substrates
from ..utils.postgresql import PostgreSQL


class AbstractPostgreSQLCharm(CharmBase, ABC):
    """An abstract PostgreSQL charm."""

    def __init__(self, *args):
        super().__init__(*args)

        # State
        self.state = CharmState(charm=self, substrate=self.substrate)

        # Managers
        self.cluster_manager = ClusterManager(
            state=self.state, workload=self.workload, client=self.postgresql
        )
        self.tls_manager = TLSManager(
            state=self.state, workload=self.workload, client=self.postgresql
        )
        self.config_manager = ConfigManager(
            state=self.state, workload=self.workload, client=self.postgresql
        )
        self.patroni_manager = PatroniManager(
            state=self.state, workload=self.workload, client=self.postgresql
        )

        # Events Handler
        self.postgresql_events_handler = PostgreSQLEventsHandler(
            self,
            self.workload,
            self.state,
            self.cluster_manager,
            self.tls_manager,
            self.config_manager,
            self.patroni_manager,
        )

        # Status Handler
        self.status_handler = StatusHandler(
            self,
            self.cluster_manager,
            self.tls_manager,
            self.config_manager,
            self.patroni_manager,
        )

    # Postgresql Client
    @property
    @abstractmethod
    def postgresql(self) -> PostgreSQL:
        """Return a PostgreSQL client."""
        pass

    # Postgresql Workload
    @property
    @abstractmethod
    def workload(self) -> BaseWorkload:
        """Access current workload."""
        pass

    # Postgresql Substrate
    @property
    @abstractmethod
    def substrate(self) -> Substrates:
        """Access current substrate."""
        pass
