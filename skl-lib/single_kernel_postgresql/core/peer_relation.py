#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""State objects for database-peers relation."""

from ops import Application, Relation, Unit

from single_kernel_postgresql.core.relation_state import RelationState
from single_kernel_postgresql.lib.charms.data_platform_libs.v0.data_interfaces import (
    DataPeerData,
    DataPeerUnitData,
)


class PostgreSQLPeer(RelationState):
    """State/Relation data collection for a PostgreSQL unit."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerUnitData,
        component: Unit,
    ):
        """Initialize the PostgreSQLPeer object."""
        super().__init__(relation, data_interface, component)
        self.unit = component


class PostgreSQLApplication(RelationState):
    """An PostgreSQL Application is the peer application state.

    This class defines state/relation data for a single PostgreSQL application.
    """

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerData,
        component: Application,
    ):
        """Initialize the PostgreSQLApplication object."""
        super().__init__(relation, data_interface, component)
        self.app = component
