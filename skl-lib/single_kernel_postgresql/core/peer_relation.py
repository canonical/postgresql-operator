#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""State objects for database-peers relation."""

import json

from ops import Application, BlockedStatus, Relation, Unit

from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.literals import (
    MONITORING_PASSWORD_KEY,
    PATRONI_PASSWORD_KEY,
    REPLICATION_PASSWORD_KEY,
    USER_PASSWORD_KEY,
)
from single_kernel_postgresql.core.relation_state import RelationState
from single_kernel_postgresql.lib.charms.data_platform_libs.v0.data_interfaces import (
    DataPeerData,
    DataPeerUnitData,
)


class PostgreSQLPeer(RelationState):
    """State/Relation data collection for a PostgreSQL unit."""

    data_interface: DataPeerUnitData
    unit: Unit

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerUnitData,
        component: Unit,
    ):
        """Initialize the PostgreSQLPeer object."""
        super().__init__(relation, data_interface, component)
        self.data_interface = data_interface
        self.unit = component

    def get_secret(self, key: str) -> str | None:
        """Get the secret value for 'key' from the peer relation data."""
        if not self.relation:
            return None
        return self.data_interface.get_secret(self.relation.id, key)

    def set_secret(self, key: str, value: str) -> None:
        """Set the secret value for 'key' in the peer relation data."""
        if not self.relation:
            return
        self.data_interface.set_secret(self.relation.id, key, value)

    def remove_secret(self, key: str) -> None:
        """Remove the secret value for 'key' from the peer relation data."""
        if not self.relation:
            return
        self.data_interface.delete_relation_data(self.relation.id, [key])

    @property
    def is_app_leader(self) -> bool:
        """Check if the current unit is the leader of the application."""
        return self.unit.is_leader()

    @property
    def is_blocked(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    @property
    def internal_cert(self) -> str | None:
        """Get internal certificate.

        Returns:
            The internal certificate from the peer relation or None if it has not yet been set by the leader.
        """
        return self.get_secret("internal-cert")

    @property
    def internal_key(self) -> str | None:
        """Get internal private key.

        Returns:
            The internal private key from the peer relation or None if it has not yet been set by the leader.
        """
        return self.get_secret("internal-key")

    @internal_cert.setter
    def internal_cert(self, value: str) -> None:
        """Set internal certificate in the peer relation."""
        self.set_secret("internal-cert", value)

    @internal_key.setter
    def internal_key(self, value: str) -> None:
        """Set internal private key in the peer relation."""
        self.set_secret("internal-key", value)

    @property
    def ip(self) -> str | None:
        """Get the unit's IP address from the peer relation data."""
        if not self.relation:
            return None
        return self.relation.data[self.unit].get("ip", "")

    @ip.setter
    def ip(self, value: str | None) -> None:
        """Set the unit's IP address in the peer relation data."""
        if not self.relation:
            return
        if value:
            self.relation.data[self.unit]["ip"] = value

    @property
    def member_name(self) -> str:
        """Get the member name for this unit."""
        return self.unit.name.replace("/", "-")

    @property
    def unit_name(self) -> str:
        """Get the unit name."""
        return self.unit.name

    @property
    def unit_id(self) -> str:
        """Get the unit id."""
        return self.unit.name.split("/")[1]

    @property
    def patroni_on_failure_condition_override(self) -> str | None:
        """Get the on-failure condition override for patroni from the peer relation data."""
        if not self.relation:
            return None
        return self.relation.data[self.unit].get("patroni-on-failure-condition-override", None)

    @property
    def database_peers_address(self) -> str | None:
        """Get the address to be used for database peers communication."""
        if not self.relation:
            return None
        return self.relation.data[self.unit].get("database-peers-address", None)

    @property
    def replication_address(self) -> str | None:
        """Get the address to be used for replication communication."""
        if not self.relation:
            return None
        return self.relation.data[self.unit].get("replication-address", None)

    @property
    def replication_offer_address(self) -> str | None:
        """Get the address to be used for replication communication in case of replication offer."""
        if not self.relation:
            return None
        return self.relation.data[self.unit].get("replication-offer-address", None)

    @property
    def private_address(self) -> str | None:
        """Get the private address of the unit."""
        if not self.relation:
            return None
        return self.relation.data[self.unit].get("private-address", None)

    @property
    def peer_addresses(self) -> set[str]:
        """Set of peer unit addresses (database, replication, and replication-offer)."""
        peer_addrs = set()
        if addr := self.database_peers_address:
            peer_addrs.add(addr)
        if addr := self.replication_address:
            peer_addrs.add(addr)
        if addr := self.replication_offer_address:
            peer_addrs.add(addr)
        if addr := (self.ip or self.private_address):
            peer_addrs.add(addr)
        return peer_addrs


class PostgreSQLApplication(RelationState):
    """An PostgreSQL Application is the peer application state.

    This class defines state/relation data for a single PostgreSQL application.
    """

    data_interface: DataPeerData
    app: Application

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerData,
        component: Application,
        substrate: Substrates,
    ):
        """Initialize the PostgreSQLApplication object."""
        super().__init__(relation, data_interface, component)
        self.app = component
        self.data_interface = data_interface
        self.substrate = substrate

    @property
    def replication_password(self) -> str | None:
        """Get replication user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret(REPLICATION_PASSWORD_KEY)

    @property
    def monitoring_password(self) -> str | None:
        """Get monitoring user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret(MONITORING_PASSWORD_KEY)

    @property
    def user_password(self) -> str | None:
        """Get operator user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret(USER_PASSWORD_KEY)

    @property
    def patroni_password(self) -> str | None:
        """Get Patroni REST API password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret(PATRONI_PASSWORD_KEY)

    @property
    def internal_ca(self) -> str | None:
        """Get internal CA.

        Returns:
            The internal CA from the peer relation or None if it has not yet been set by the leader.
        """
        return self.get_secret("internal-ca")

    @property
    def internal_ca_key(self) -> str | None:
        """Get internal CA private key.

        Returns:
            The internal CA private key from the peer relation or None if it has not yet been set by the leader.
        """
        return self.get_secret("internal-ca-key")

    @property
    def cluster_name(self) -> str:
        """Get cluster name.

        Returns:
            The cluster name, which is the same as the application name.
        """
        if self.substrate == Substrates.K8S:
            return f"patroni-{self.app.name}"
        return self.app.name

    @property
    def planned_units(self) -> int:
        """Get the number of planned units for the application."""
        return self.app.planned_units()

    @property
    def members_ips(self) -> set[str]:
        """Returns the list of IPs addresses of the current members of the cluster."""
        if not self.relation:
            return set()
        return set(json.loads(self.relation.data[self.app].get("members_ips", "[]")))

    @property
    def endpoints(self) -> set[str]:
        """Returns the list of endpoints of the current members of the cluster."""
        if not self.relation:
            return set()
        return set(json.loads(self.relation.data[self.app].get("endpoints", "[]")))

    def get_secret(self, key: str) -> str | None:
        """Get the secret value for 'key' from the peer relation data."""
        if not self.relation:
            return None
        return self.data_interface.get_secret(self.relation.id, key)

    def set_secret(self, key: str, value: str) -> None:
        """Set the secret value for 'key' in the peer relation data."""
        if not self.relation:
            return
        self.data_interface.set_secret(self.relation.id, key, value)

    def remove_secret(self, key: str) -> None:
        """Remove the secret value for 'key' from the peer relation data."""
        if not self.relation:
            return
        self.data_interface.delete_relation_data(self.relation.id, [key])
