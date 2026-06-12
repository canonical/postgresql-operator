# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Skeleton for the abstract charm."""

from abc import ABC, abstractmethod

from ops.charm import CharmBase

from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.events.postgresql import PostgreSQLEventsHandler
from single_kernel_postgresql.managers.cluster import ClusterManager
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

        # Events Handler
        self.postgresql_events_handler = PostgreSQLEventsHandler(self)

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
