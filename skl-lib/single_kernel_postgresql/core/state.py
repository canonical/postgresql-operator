# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.


"""Object representing the global state of PostgreSQL Charm."""

from typing import TYPE_CHECKING

from data_platform_helpers.advanced_statuses import StatusesState
from ops import JujuVersion, Object, Relation, Unit

from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.literals import PEER_RELATION, STATUS_PEERS_RELATION
from single_kernel_postgresql.core.peer_relation import PostgreSQLApplication, PostgreSQLPeer
from single_kernel_postgresql.lib.charms.data_platform_libs.v0.data_interfaces import (
    DataPeerData,
    DataPeerUnitData,
)

if TYPE_CHECKING:
    from single_kernel_postgresql.charms.abstract_charm import AbstractPostgreSQLCharm


class CharmState(Object):
    """The global PostgreSQL Charm State."""

    def __init__(
        self,
        charm: "AbstractPostgreSQLCharm",
        substrate: Substrates,
    ) -> None:
        """Initialize the CharmState object."""
        super().__init__(charm, "charm_state")
        self.substrate = substrate
        self.peer_app_interface = DataPeerData(model=charm.model, relation_name=PEER_RELATION)
        self.peer_unit_interface = DataPeerUnitData(model=charm.model, relation_name=PEER_RELATION)

        self.statuses = StatusesState(self, STATUS_PEERS_RELATION)

    # -- Relations
    @property
    def peer_relation(self) -> Relation | None:
        """Get charm peer relation."""
        return self.model.get_relation(PEER_RELATION)

    @property
    def status_peers_relation(self) -> Relation | None:
        """Get status peers relation."""
        return self.model.get_relation(STATUS_PEERS_RELATION)

    # -- Core State Components

    @property
    def peer(self) -> PostgreSQLPeer:
        """Get the PostgreSQL peer state."""
        return PostgreSQLPeer(
            relation=self.peer_relation,
            data_interface=self.peer_unit_interface,
            component=self.model.unit,
        )

    @property
    def all_application_units(self) -> list[Unit]:
        """Fetch the list of units for the current app."""
        if not self.peer_relation:
            return []
        return [u for u in self.peer_relation.units.union({self.peer.unit}) if isinstance(u, Unit)]

    @property
    def application_peers(self) -> list[PostgreSQLPeer]:
        """Return all PostgreSQL peers using peer relation."""
        return [
            PostgreSQLPeer(
                relation=self.peer_relation,
                data_interface=self.peer_unit_interface,
                component=unit,
            )
            for unit in self.all_application_units
        ]

    @property
    def application(self) -> PostgreSQLApplication:
        """Get the PostgreSQL application state."""
        return PostgreSQLApplication(
            relation=self.peer_relation,
            data_interface=self.peer_app_interface,
            component=self.model.app,
        )

    # -- Cluster State Properties

    @property
    def implements_secrets(self):
        """Property to cache results from a Juju call."""
        return JujuVersion.from_environ().has_secrets
