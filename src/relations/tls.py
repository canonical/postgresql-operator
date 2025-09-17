# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""TLS Handler."""

import logging
import socket
from datetime import timedelta
from typing import TYPE_CHECKING

from charms.tls_certificates_interface.v4.tls_certificates import (
    Certificate,
    CertificateRequestAttributes,
    PrivateKey,
    TLSCertificatesRequiresV4,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from ops import (
    EventSource,
)
from ops.framework import EventBase, Object
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.pebble import PathError, ProtocolError
from tenacity import RetryError

from constants import (
    APP_SCOPE,
    UNIT_SCOPE,
)

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)
SCOPE = "unit"
TLS_CLIENT_RELATION = "client-certificates"
TLS_PEER_RELATION = "peer-certificates"


class RefreshTLSCertificatesEvent(EventBase):
    """Event for refreshing TLS certificates."""


class TlsError(Exception):
    """TLS implementation internal exception."""


class TLS(Object):
    """In this class we manage certificates relation."""

    refresh_tls_certificates_event = EventSource(RefreshTLSCertificatesEvent)

    def _get_client_addrs(self) -> set[str]:
        client_addrs = set()
        if addr := self.charm.unit_peer_data.get("database-address"):
            client_addrs.add(addr)
        return client_addrs

    def _get_peer_addrs(self) -> set[str]:
        peer_addrs = set()
        if addr := self.charm.unit_peer_data.get("database-peers-address"):
            peer_addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("replication-address"):
            peer_addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("replication-offer-address"):
            peer_addrs.add(addr)
        if addr := self.charm.unit_peer_data.get("private-address"):
            peer_addrs.add(addr)
        return peer_addrs

    def _get_common_name(self) -> str:
        return self.charm.unit_peer_data.get("database-address") or self.host

    def _get_peer_common_name(self) -> str:
        return self.charm.unit_peer_data.get("database-peers-address") or self.host

    def __init__(self, charm: "PostgresqlOperatorCharm", peer_relation: str):
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        unit_id = self.charm.unit.name.split("/")[1]
        self.host = f"{self.charm.app.name}-{unit_id}"
        if self.charm.unit_peer_data:
            client_addresses = self._get_client_addrs()
            peer_addresses = self._get_peer_addrs()
        else:
            client_addresses = set()
            peer_addresses = set()
        self.common_hosts = {self.host}
        if fqdn := socket.getfqdn():
            self.common_hosts.add(fqdn)

        self.client_certificate = TLSCertificatesRequiresV4(
            self.charm,
            TLS_CLIENT_RELATION,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self._get_common_name(),
                    sans_ip=frozenset(client_addresses),
                    sans_dns=frozenset({
                        *self.common_hosts,
                        # IP address need to be part of the DNS SANs list due to
                        # https://github.com/pgbackrest/pgbackrest/issues/1977.
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
                    common_name=self._get_peer_common_name(),
                    sans_ip=frozenset(self._get_peer_addrs()),
                    sans_dns=frozenset({
                        *self.common_hosts,
                        # IP address need to be part of the DNS SANs list due to
                        # https://github.com/pgbackrest/pgbackrest/issues/1977.
                        *peer_addresses,
                    }),
                ),
            ],
            refresh_events=[self.refresh_tls_certificates_event],
        )

        self.framework.observe(
            self.client_certificate.on.certificate_available, self._on_certificate_available
        )
        self.framework.observe(
            self.peer_certificate.on.certificate_available, self._on_peer_certificate_available
        )

        self.framework.observe(
            self.charm.on[TLS_CLIENT_RELATION].relation_broken, self._on_certificate_available
        )
        self.framework.observe(
            self.charm.on[TLS_PEER_RELATION].relation_broken, self._on_peer_certificate_available
        )

    def _on_peer_certificate_available(self, event: EventBase) -> None:
        certs, _ = self.peer_certificate.get_assigned_certificates()
        new_ca = str(certs[0].ca) if certs else None
        current_ca = self.charm.get_secret(UNIT_SCOPE, "current-ca")
        # Stash the CAs in case of rotation
        if new_ca != current_ca:
            self.charm.set_secret(UNIT_SCOPE, "current-ca", new_ca)
            self.charm.set_secret(UNIT_SCOPE, "old-ca", current_ca)
        self._on_certificate_available(event)

    def _on_certificate_available(self, event: EventBase) -> None:
        if not self.charm.get_secret(APP_SCOPE, "internal-ca"):
            logger.debug("Charm not ready yet")
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

    def get_client_tls_files(self) -> tuple[str | None, str | None, str | None]:
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

    def get_peer_tls_files(self) -> tuple[str | None, str | None, str | None]:
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
        if not all((key, ca_file, cert)):
            key = self.charm.get_secret(UNIT_SCOPE, "internal-key")
            cert = self.charm.get_secret(UNIT_SCOPE, "internal-cert")
            ca_file = self.charm.get_secret(APP_SCOPE, "internal-ca")
        return key, ca_file, cert

    def get_peer_ca_bundle(self) -> str:
        """Get bundled CA certs."""
        certs, _ = self.peer_certificate.get_assigned_certificates()
        operator_ca = str(certs[0].ca) if certs else ""
        old_operator_ca = self.charm.get_secret(UNIT_SCOPE, "old-ca") or ""
        internal_ca = self.charm.get_secret(APP_SCOPE, "internal-ca") or ""
        return "\n".join((operator_ca, old_operator_ca, internal_ca))

    def generate_internal_peer_ca(self) -> None:
        """Generate internal peer CA using the tls lib."""
        private_key = generate_private_key()
        ca = generate_ca(
            private_key,
            common_name=f"{self.charm.app.name}-{self.charm.model.uuid}",
            validity=timedelta(days=7300),
        )
        logger.warning("Internal peer CA generated. Please use a proper TLS operator if possible.")
        self.charm.set_secret(APP_SCOPE, "internal-ca-key", str(private_key))
        self.charm.set_secret(APP_SCOPE, "internal-ca", str(ca))

    def generate_internal_peer_cert(self) -> None:
        """Generate internal peer certificate using the tls lib."""
        if not (ca_key_secret := self.charm.get_secret(APP_SCOPE, "internal-ca-key")):
            raise TlsError("No CA key content.")
        ca_key = PrivateKey.from_string(ca_key_secret)
        if not (ca_secret := self.charm.get_secret(APP_SCOPE, "internal-ca")):
            raise TlsError("No CA cert content.")
        ca = Certificate.from_string(ca_secret)
        private_key = generate_private_key()
        csr = generate_csr(
            private_key,
            common_name=self._get_peer_common_name(),
            sans_ip=frozenset(self._get_peer_addrs()),
            sans_dns=frozenset({
                *self.common_hosts,
                # IP address need to be part of the DNS SANs list due to
                # https://github.com/pgbackrest/pgbackrest/issues/1977.
                *self._get_peer_addrs(),
            }),
        )
        cert = generate_certificate(csr, ca, ca_key, validity=timedelta(days=7300))
        self.charm.set_secret(UNIT_SCOPE, "internal-key", str(private_key))
        self.charm.set_secret(UNIT_SCOPE, "internal-cert", str(cert))
        self.charm.push_tls_files_to_workload()
