# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""TLS Transfer Handler."""

import logging
from collections.abc import Iterator

from charms.certificate_transfer_interface.v0.certificate_transfer import (
    CertificateAvailableEvent,
    CertificateRemovedEvent,
    CertificateTransferRequires,
)
from ops.framework import Object
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.pebble import PathError, ProtocolError
from tenacity import RetryError

logger = logging.getLogger(__name__)
SCOPE = "unit"
TLS_TRANSFER_RELATION = "receive-ca-cert"


class TLSTransfer(Object):
    """In this class we manage certificate transfer relation."""

    def __init__(self, charm, peer_relation: str):
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        self.certs_transfer = CertificateTransferRequires(self.charm, TLS_TRANSFER_RELATION)
        self.framework.observe(
            self.certs_transfer.on.certificate_available, self._on_certificate_available
        )
        self.framework.observe(
            self.certs_transfer.on.certificate_removed, self._on_certificate_removed
        )

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Enable TLS when TLS certificate is added."""
        relation = self.charm.model.get_relation(TLS_TRANSFER_RELATION, event.relation_id)
        if relation is None:
            logger.error("Relationship not established anymore.")
            return

        secret_name = f"ca-{relation.app.name}"
        self.charm.set_secret(SCOPE, secret_name, event.ca)

        try:
            if not self.charm.push_ca_file_into_workload(secret_name):
                logger.debug("Cannot push TLS certificates at this moment")
                event.defer()
                return
        except (PebbleConnectionError, PathError, ProtocolError, RetryError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
            event.defer()
            return

    def _on_certificate_removed(self, event: CertificateRemovedEvent) -> None:
        """Disable TLS when TLS certificate is removed."""
        relation = self.charm.model.get_relation(TLS_TRANSFER_RELATION, event.relation_id)
        if relation is None:
            logger.error("Relationship not established anymore.")
            return

        secret_name = f"ca-{relation.app.name}"
        self.charm.set_secret(SCOPE, secret_name, None)

        try:
            if not self.charm.clean_ca_file_from_workload(secret_name):
                logger.debug("Cannot clean CA certificates at this moment")
                event.defer()
                return
        except (PebbleConnectionError, PathError, ProtocolError, RetryError) as e:
            logger.error("Cannot clean CA certificates: %r", e)
            event.defer()
            return

    def get_ca_secret_names(self) -> Iterator[str]:
        """Get a secret-name for each relation fulfilling the CA transfer interface.

        Returns:
            Secret name for a CA transfer fulfilled interface.
        """
        relations = self.charm.model.relations.get(TLS_TRANSFER_RELATION, [])

        for relation in relations:
            yield f"ca-{relation.app.name}"
