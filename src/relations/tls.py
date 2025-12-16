# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""TLS Handler."""

import logging
import socket
from typing import TYPE_CHECKING

from charmlibs.interfaces.tls_certificates import (
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)
from ops import (
    EventSource,
)
from ops.framework import EventBase, Object
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.pebble import PathError, ProtocolError
from tenacity import RetryError

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)
SCOPE = "unit"
TLS_RELATION = "certificates"


class RefreshTLSCertificatesEvent(EventBase):
    """Event for refreshing TLS certificates."""


class TlsError(Exception):
    """TLS implementation internal exception."""


class TLS(Object):
    """In this class we manage certificates relation."""

    refresh_tls_certificates_event = EventSource(RefreshTLSCertificatesEvent)

    def _get_addrs(self) -> set[str]:
        addrs = set()
        if addr := self.charm.unit_peer_data.get("database-address"):
            addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("database-peers-address"):
            addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("replication-address"):
            addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("replication-offer-address"):
            addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("private-address"):
            addrs.add(addr)
        return addrs

    def _get_common_name(self) -> str:
        return self.charm.unit_peer_data.get("database-address") or self.host

    def __init__(self, charm: "PostgresqlOperatorCharm", peer_relation: str):
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        unit_id = self.charm.unit.name.split("/")[1]
        self.host = f"{self.charm.app.name}-{unit_id}"
        addresses = self._get_addrs() if self.charm.unit_peer_data else set()
        self.common_hosts = {self.host}
        if fqdn := socket.getfqdn():
            self.common_hosts.add(fqdn)

        self.certificate = TLSCertificatesRequiresV4(
            self.charm,
            TLS_RELATION,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self._get_common_name(),
                    sans_ip=frozenset(addresses),
                    sans_dns=frozenset({
                        *self.common_hosts,
                        # IP address need to be part of the DNS SANs list due to
                        # https://github.com/pgbackrest/pgbackrest/issues/1977.
                        *addresses,
                    }),
                ),
            ],
            refresh_events=[self.refresh_tls_certificates_event],
            private_key=None,
        )

        self.framework.observe(
            self.certificate.on.certificate_available, self._on_certificate_available
        )

        self.framework.observe(
            self.charm.on[TLS_RELATION].relation_broken, self._on_certificate_available
        )

    def _on_certificate_available(self, event: EventBase) -> None:
        if not self.charm.is_cluster_initialised:
            logger.debug("Cluster not initialised yet")
            event.defer()
            return
        try:
            if not self.charm.push_tls_files_to_workload():
                logger.debug("Cannot push TLS certificates at this moment")
                event.defer()
                return
        except (PebbleConnectionError, PathError, ProtocolError, RetryError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
            event.defer()
            return

    def get_tls_files(self) -> tuple[str | None, str | None, str | None]:
        """Prepare TLS files in special PostgreSQL way.

        PostgreSQL needs three files:
        — CA file should have a full chain.
        — Key file should have private key.
        — Certificate file should have certificate without certificate chain.
        """
        ca_file = None
        cert = None
        key = None
        certs, private_key = self.certificate.get_assigned_certificates()
        if private_key:
            key = str(private_key)
        if certs:
            cert = str(certs[0].certificate)
            ca_file = str(certs[0].ca)
        return key, ca_file, cert
