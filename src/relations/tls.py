# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""TLS Handler."""

import logging
import socket
from typing import TYPE_CHECKING

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)
from ops import (
    EventSource,
    RelationBrokenEvent,
)
from ops.framework import EventBase, Object
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.pebble import PathError, ProtocolError
from tenacity import RetryError

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)
SCOPE = "unit"
TLS_CLIENT_RELATION = "client-certificates"
TLS_PEER_RELATION = "peer-certificates"
TLS_RELS = (TLS_CLIENT_RELATION, TLS_PEER_RELATION)


class RefreshTLSCertificatesEvent(EventBase):
    """Event for refreshing TLS certificates."""


class TLS(Object):
    """In this class we manage certificates relation."""

    refresh_tls_certificates_event = EventSource(RefreshTLSCertificatesEvent)

    def __init__(self, charm: "PostgresqlOperatorCharm", peer_relation: str):
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        unit_id = self.charm.unit.name.split("/")[1]
        host = f"{self.charm.app.name}-{unit_id}"
        if self.charm.unit_peer_data:
            common_name = self.charm.unit_peer_data.get("database-address") or host
            client_addresses = {
                self.charm.unit_peer_data.get("database-address"),
            }
            peer_addresses = {
                self.charm.unit_peer_data.get("database-peers-address"),
                self.charm.unit_peer_data.get("replication-address"),
                self.charm.unit_peer_data.get("replication-offer-address"),
                self.charm.unit_peer_data.get("private-address"),
            }
            client_addresses -= {None}
            peer_addresses -= {None}
        else:
            common_name = host
            client_addresses = set()
            peer_addresses = set()

        self.client_certificate = TLSCertificatesRequiresV4(
            self.charm,
            TLS_CLIENT_RELATION,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=common_name,
                    sans_ip=frozenset(client_addresses),
                    sans_dns=frozenset({
                        host,
                        socket.getfqdn(),
                        *client_addresses,
                    }),
                ),
            ],
            refresh_events=[self.refresh_tls_certificates_event],
        )
        self.peer_certificate = TLSCertificatesRequiresV4(
            self.charm,
            TLS_PEER_RELATION,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=common_name,
                    sans_ip=frozenset(peer_addresses),
                    sans_dns=frozenset({
                        host,
                        socket.getfqdn(),
                        *peer_addresses,
                    }),
                ),
            ],
            refresh_events=[self.refresh_tls_certificates_event],
        )

        self.framework.observe(
            self.client_certificate.on.certificate_available,
            self._on_certificate_available,
        )
        self.framework.observe(
            self.peer_certificate.on.certificate_available,
            self._on_certificate_available,
        )

        for rel in TLS_RELS:
            self.framework.observe(
                self.charm.on[rel].relation_broken, self._on_certificates_broken
            )

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        try:
            if not self.charm.push_tls_files_to_workload():
                logger.debug("Cannot push TLS certificates at this moment")
                event.defer()
                return
        except (PebbleConnectionError, PathError, ProtocolError, RetryError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
            event.defer()
            return

    def _on_certificates_broken(self, event: RelationBrokenEvent) -> None:
        if not self.charm.update_config():
            logger.debug("Cannot update config at this moment")
            event.defer()

    def get_client_tls_files(self) -> (str | None, str | None, str | None):
        """Prepare TLS files in special PostgreSQL way.

        PostgreSQL needs three files:
        — CA file should have a full chain.
        — Key file should have private key.
        — Certificate file should have certificate without certificate chain.
        """
        ca_file = None
        cert = None
        key = None
        certs, private_key = self.client_certificate.get_assigned_certificates()
        if private_key:
            key = str(private_key)
        if certs:
            cert = str(certs[0].certificate)
            ca_file = str(certs[0].ca)
        return key, ca_file, cert

    def get_peer_tls_files(self) -> (str | None, str | None, str | None):
        """Prepare TLS files in special PostgreSQL way.

        PostgreSQL needs three files:
        — CA file should have a full chain.
        — Key file should have private key.
        — Certificate file should have certificate without certificate chain.
        """
        ca_file = None
        cert = None
        key = None
        certs, private_key = self.peer_certificate.get_assigned_certificates()
        if private_key:
            key = str(private_key)
        if certs:
            cert = str(certs[0].certificate)
            ca_file = str(certs[0].ca)
        return key, ca_file, cert
