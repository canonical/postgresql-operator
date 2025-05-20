# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""In this class we manage certificates relation.

This class handles certificate request and renewal through
the interaction with the TLS Certificates Operator.

This library needs that the following libraries are imported to work:
- https://charmhub.io/certificate-transfer-interface/libraries/certificate_transfer
- https://charmhub.io/tls-certificates-interface/libraries/tls_certificates

It also needs the following methods in the charm class:
— get_hostname_by_unit: to retrieve the DNS hostname of the unit.
— get_secret: to retrieve TLS files from secrets.
— push_tls_files_to_workload: to push TLS files to the workload container and enable TLS.
— set_secret: to store TLS files as secrets.
— update_config: to disable TLS when relation with the TLS Certificates Operator is broken.
"""

import logging
import socket
from hashlib import shake_128

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)
from ops import (
    RelationBrokenEvent,
    RelationCreatedEvent,
)
from ops.framework import Object
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.pebble import PathError, ProtocolError
from tenacity import RetryError

logger = logging.getLogger(__name__)
SCOPE = "unit"
TLS_CREATION_RELATION = "certificates"


class TLS(Object):
    """In this class we manage certificates relation."""

    def __init__(self, charm, peer_relation: str):
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        unit_id = self.charm.unit.name.split("/")[1]
        # TODO check and add spaces ips
        ip = socket.gethostbyname(socket.gethostname())
        addresses = {ip}

        self.certificate = TLSCertificatesRequiresV4(
            self.charm,
            TLS_CREATION_RELATION,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=ip,
                    sans_ip=frozenset(addresses),
                    sans_dns=frozenset({
                        f"{self.charm.app.name}-{unit_id}",
                        socket.getfqdn(),
                        *addresses,
                    }),
                ),
            ],
        )

        self.framework.observe(
            self.certificate.on.certificate_available,
            self._on_certificate_available,
        )

        self.framework.observe(
            self.charm.on[TLS_CREATION_RELATION].relation_created, self._on_relation_created
        )
        self.framework.observe(
            self.charm.on[TLS_CREATION_RELATION].relation_broken, self._on_certificates_broken
        )

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        pass

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

    def get_tls_files(self) -> (str | None, str | None, str | None):
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

    def get_cert_hash(self) -> str:
        """Generate hash of the cert chain."""
        _, ca_file, cert = self.get_tls_files()
        return shake_128((str(ca_file) + str(cert)).encode()).hexdigest(16)
