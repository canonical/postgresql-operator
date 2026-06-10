# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.


"""Object representing the global state of PostgreSQL Charm."""

import re
import socket
from functools import cached_property
from typing import TYPE_CHECKING, Any, get_args

from data_platform_helpers.advanced_statuses import StatusesState, StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope
from ops import JujuVersion, ModelError, Object, Relation, SecretNotFoundError, Unit

from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.literals import (
    APP_SCOPE,
    PEER_RELATION,
    SCOPES,
    STATUS_PEERS_RELATION,
)
from single_kernel_postgresql.core.config import CharmConfig
from single_kernel_postgresql.core.peer_relation import PostgreSQLApplication, PostgreSQLPeer
from single_kernel_postgresql.lib.charms.data_platform_libs.v0.data_interfaces import (
    DataPeerData,
    DataPeerUnitData,
)
from single_kernel_postgresql.utils import unit_name_to_pod_name
from single_kernel_postgresql.utils.secret import translate_field_to_secret_key
from single_kernel_postgresql.utils.status import format_status

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

    # -- Charm Config
    @cached_property
    def config(self) -> CharmConfig:
        """Return a config instance validated and parsed using the provided pydantic class."""
        config = {
            # Prefer value of option name with dash (-) and fallback to name with underscore (_)
            config_option: self.model.config.get(
                config_option.replace("_", "-"), self.model.config.get(config_option)
            )
            for config_option in CharmConfig.keys()  # noqa: SIM118
        }
        config: dict[str, Any] = {
            config_option: value for config_option, value in config.items() if value is not None
        }
        return CharmConfig(**config)

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
            substrate=self.substrate,
        )

    # -- Cluster state utilities
    def _get_hostname_from_unit(self, member: str) -> str:
        """Create a DNS name for a PostgreSQL/Patroni cluster member.

        Args:
            member: the Patroni member name, e.g. "postgresql-k8s-0".

        Returns:
            A string representing the hostname of the PostgreSQL unit.
        """
        unit_id = member.split("-")[-1]
        return f"{self.model.app.name}-{unit_id}.{self.model.app.name}-endpoints"

    # -- Cluster State Properties

    @property
    def implements_secrets(self):
        """Property to cache results from a Juju call."""
        return JujuVersion.from_environ().has_secrets

    @property
    def internal_peer_ca_common_name(self) -> str:
        """Return the common name for the internally generated peer CA."""
        return f"{self.model.app.name}-{self.model.uuid}"

    @property
    def unit_ip(self) -> str | None:
        """Current unit ip."""
        if binding := self.model.get_binding(PEER_RELATION):
            return str(binding.network.bind_address)

    @property
    def fqdn(self) -> str | None:
        """Current unit fqdn."""
        if self.substrate == Substrates.K8S:
            return self._get_hostname_from_unit(unit_name_to_pod_name(self.model.unit.name))
        else:
            return socket.getfqdn()

    @property
    def endpoint(self) -> str | None:
        """Current unit endpoint."""
        if self.substrate == Substrates.K8S:
            return self.fqdn
        else:
            return self.unit_ip

    @property
    def endpoints(self) -> set[str]:
        """Returns the list of endpoints of the current members of the cluster."""
        if self.peer_relation:
            return self.application.endpoints
        else:
            return {self.endpoint} if self.endpoint else set()

    @property
    def model_name(self) -> str:
        """Current model name."""
        return self.model.name

    @cached_property
    def patroni_url(self) -> str:
        """Patroni REST API URL."""
        return f"https://{self.unit_ip}:8007"

    @property
    def peer_members_ips(self) -> set[str]:
        """Fetch current list of peer members IPs.

        Returns:
            A list of peer members addresses (strings).
        """
        # Get all members IPs and remove the current unit IP from the list.
        addresses = self.application.members_ips
        current_unit_ip = self.unit_ip
        if current_unit_ip in addresses:
            addresses.remove(current_unit_ip)
        return addresses

    @property
    def host(self) -> str:
        """Current unit host."""
        return f"{self.model.app.name}-{self.peer.unit_id}"

    @property
    def common_hosts(self) -> set[str]:
        """Common hosts to be used in TLS certificate SANs."""
        return {self.host, self.fqdn} if self.fqdn else {self.host}

    @property
    def peer_common_name(self) -> str:
        """Return the common name for the internally generated peer certificate."""
        return self.peer.database_peers_address or self.host

    # -- Secrets
    # TODO: This is temporary till data interfaces v1 is integrated
    def get_secret(self, scope: SCOPES, key: str) -> str | None:
        """Get secret from the secret storage."""
        if scope not in get_args(SCOPES):
            raise RuntimeError("Unknown secret scope.")

        if not self.peer_relation:
            return None
        secret_key = translate_field_to_secret_key(key)
        if scope == APP_SCOPE:
            return self.application.get_secret(secret_key)
        else:
            return self.peer.get_secret(secret_key)

    def set_secret(self, scope: SCOPES, key: str, value: str | None) -> str | None:
        """Set secret from the secret storage."""
        if scope not in get_args(SCOPES):
            raise RuntimeError("Unknown secret scope.")

        if not value:
            return self.remove_secret(scope, key)

        if not self.peer_relation:
            return None
        secret_key = translate_field_to_secret_key(key)
        if scope == APP_SCOPE:
            self.application.set_secret(secret_key, value)
        else:
            self.peer.set_secret(secret_key, value)

    def remove_secret(self, scope: SCOPES, key: str) -> None:
        """Removing a secret."""
        if scope not in get_args(SCOPES):
            raise RuntimeError("Unknown secret scope.")

        if not self.peer_relation:
            return None
        secret_key = translate_field_to_secret_key(key)
        if scope == APP_SCOPE:
            self.application.remove_secret(secret_key)
        else:
            self.peer.remove_secret(secret_key)

    def get_secret_from_id(self, secret_id: str) -> dict[str, str]:
        """Resolve the given id of a Juju secret and return the content as a dict.

        This method can be used to retrieve any secret, not just those used via the peer relation.
        If the secret is not owned by the charm, it has to be granted access to it.

        Args:
            secret_id (str): The id of the secret.

        Returns:
            dict: The content of the secret.
        """
        try:
            secret_content = self.model.get_secret(id=secret_id).get_content(refresh=True)
        except (SecretNotFoundError, ModelError):
            raise

        return secret_content

    # -- Statuses
    def add_status_if_not_present(
        self,
        status: StatusObject,
        scope: AdvancedStatusesScope,
        component: str,
        dynamic_params: dict[str, Any] | None = None,
        search_parameters: dict[str, Any] | None = None,
    ) -> None:
        """Add charm status if not present already.

        Args:
            status: charm status to be added.
            scope: scope of the added charm status.
            component: name of the responsible component of the added status.
            dynamic_params: params to format added status message with.
            search_parameters: params to format searched status message with prior to interpolated
                search. Helps to differentiate between statuses with multiple dynamic parameters.
                For example, if one of the parameters is a relation id, you want for search to be
                performed only through specific relation, while other parameters should be loosen
                by search regex. E.g. if you have a two parameters `relation_id` and `exception`,
                you may want to add a status with {"relation_id": 1, "exception": "err"} but with
                search parameters {"relation_id": 1, "exception": "{}"} in order to not override
                the same statuses from different relations. Note: "{}" placeholder makes
                parameter loosen.
        """
        if scope == "app" and not self.peer.is_app_leader:
            return

        present_statuses = self.statuses.get(scope, component)

        if not dynamic_params and status not in present_statuses:
            self.statuses.add(status, scope, component)

        if dynamic_params and (
            not (
                present_status := self._search_interpolated_status(
                    status, scope, component, search_parameters
                )
            )
            or present_status.message != format_status(status, dynamic_params).message
        ):
            # Updates dynamic params if status already present.
            self.remove_status_if_present(status, scope, component, interpolated=True)
            self.statuses.add(format_status(status, dynamic_params), scope, component)

    def remove_status_if_present(
        self,
        status: StatusObject,
        scope: AdvancedStatusesScope,
        component: str,
        interpolated: bool = False,
        search_parameters: dict[str, Any] | None = None,
    ) -> None:
        """Remove charm status if it is present.

        Args:
            status: charm status to be removed.
            scope: scope of the removed charm status.
            component: name of the responsible component of the removed status.
            interpolated: perform a regex search by the status message to find
                statuses formatted with dynamic parameters.
            search_parameters: params to format searched status message with prior to interpolated
                search. Helps to differentiate between statuses with multiple dynamic parameters.
                Note: "{}" placeholder makes parameter loosen.
        """
        if scope == "app" and not self.peer.is_app_leader:
            return

        present_statuses = self.statuses.get(scope, component)

        if not interpolated and status in present_statuses:
            self.statuses.delete(status, scope, component)

        if interpolated and (
            present_status := self._search_interpolated_status(
                status, scope, component, search_parameters
            )
        ):
            self.statuses.delete(present_status, scope, component)

    def _search_interpolated_status(
        self,
        status: StatusObject,
        scope: AdvancedStatusesScope,
        component: str,
        interpolated_parameters: dict[str, Any] | None = None,
    ) -> StatusObject | None:
        """Remove charm status if it is present.

        Args:
            status: charm status to be removed.
            scope: scope of the removed charm status.
            component: name of the responsible component of the removed status.
            interpolated_parameters: params to format searched status message with prior to
                interpolated search. Helps to differentiate between statuses with multiple
                dynamic parameters. Note: "{}" placeholder makes parameter loosen.

        Returns:
            status if it was found.
        """
        regex_pattern = re.sub(
            r"\{.*?\}",
            r"(?s:.*?)",
            format_status(status, interpolated_parameters).message,
        )
        for present_status in self.statuses.get(scope, component):
            if re.fullmatch(regex_pattern, present_status.message) is not None:
                return present_status
        return None
