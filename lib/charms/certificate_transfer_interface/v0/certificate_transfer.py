# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library for the certificate_transfer relation.

This library contains the Requires and Provides classes for handling the
ertificate-transfer interface.

## Getting Started
From a charm directory, fetch the library using `charmcraft`:

```shell
charmcraft fetch-lib charms.certificate_transfer_interface.v0.certificate_transfer
```

### Provider charm
The provider charm is the charm providing public certificates to another charm that requires them.

Example:
```python
from ops.charm import CharmBase, RelationJoinedEvent
from ops.main import main

from lib.charms.certificate_transfer_interface.v0.certificate_transfer import(
    CertificateTransferProvides,
)


class DummyCertificateTransferProviderCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.certificate_transfer = CertificateTransferProvides(self, "certificates")
        self.framework.observe(
            self.on.certificates_relation_joined, self._on_certificates_relation_joined
        )

    def _on_certificates_relation_joined(self, event: RelationJoinedEvent):
        certificate = "my certificate"
        ca = "my CA certificate"
        chain = ["certificate 1", "certificate 2"]
        self.certificate_transfer.set_certificate(
            certificate=certificate, ca=ca, chain=chain, relation_id=event.relation.id
        )


if __name__ == "__main__":
    main(DummyCertificateTransferProviderCharm)
```

### Requirer charm
The requirer charm is the charm requiring certificates from another charm that provides them.

Example:
```python

from ops.charm import CharmBase
from ops.main import main

from lib.charms.certificate_transfer_interface.v0.certificate_transfer import (
    CertificateAvailableEvent,
    CertificateRemovedEvent,
    CertificateTransferRequires,
)


class DummyCertificateTransferRequirerCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.certificate_transfer = CertificateTransferRequires(self, "certificates")
        self.framework.observe(
            self.certificate_transfer.on.certificate_available, self._on_certificate_available
        )
        self.framework.observe(
            self.certificate_transfer.on.certificate_removed, self._on_certificate_removed
        )

    def _on_certificate_available(self, event: CertificateAvailableEvent):
        print(event.certificate)
        print(event.ca)
        print(event.chain)
        print(event.relation_id)

    def _on_certificate_removed(self, event: CertificateRemovedEvent):
        print(event.relation_id)


if __name__ == "__main__":
    main(DummyCertificateTransferRequirerCharm)
```

You can relate both charms by running:

```bash
juju relate <certificate_transfer provider charm> <certificate_transfer requirer charm>
```

"""

import json
import logging
from typing import List, Mapping

from jsonschema import exceptions, validate  # type: ignore[import-untyped]
from ops import Relation
from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
)
from ops.framework import EventBase, EventSource, Handle, Object

# The unique Charmhub library identifier, never change it
LIBID = "3785165b24a743f2b0c60de52db25c8b"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 11

PYDEPS = ["jsonschema"]


logger = logging.getLogger(__name__)


PROVIDER_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "$id": "https://canonical.github.io/charm-relation-interfaces/interfaces/certificate_transfer/schemas/provider.json",
    "type": "object",
    "title": "`certificate_transfer` provider schema",
    "description": "The `certificate_transfer` root schema comprises the entire provider application databag for this interface.",
    "default": {},
    "examples": [
        {
            "certificate": "-----BEGIN CERTIFICATE-----\nMIIC6DCCAdCgAwIBAgIUW42TU9LSjEZLMCclWrvSwAsgRtcwDQYJKoZIhvcNAQEL\nBQAwIDELMAkGA1UEBhMCVVMxETAPBgNVBAMMCHdoYXRldmVyMB4XDTIzMDMyNDE4\nNDMxOVoXDTI0MDMyMzE4NDMxOVowPDELMAkGA1UEAwwCb2sxLTArBgNVBC0MJGUw\nNjVmMWI3LTE2OWEtNDE5YS1iNmQyLTc3OWJkOGM4NzIwNjCCASIwDQYJKoZIhvcN\nAQEBBQADggEPADCCAQoCggEBAK42ixoklDH5K5i1NxXo/AFACDa956pE5RA57wlC\nBfgUYaIDRmv7TUVJh6zoMZSD6wjSZl3QgP7UTTZeHbvs3QE9HUwEkH1Lo3a8vD3z\neqsE2vSnOkpWWnPbfxiQyrTm77/LAWBt7lRLRLdfL6WcucD3wsGqm58sWXM3HG0f\nSN7PHCZUFqU6MpkHw8DiKmht5hBgWG+Vq3Zw8MNaqpwb/NgST3yYdcZwb58G2FTS\nZvDSdUfRmD/mY7TpciYV8EFylXNNFkth8oGNLunR9adgZ+9IunfRKj1a7S5GSwXU\nAZDaojw+8k5i3ikztsWH11wAVCiLj/3euIqq95z8xGycnKcCAwEAATANBgkqhkiG\n9w0BAQsFAAOCAQEAWMvcaozgBrZ/MAxzTJmp5gZyLxmMNV6iT9dcqbwzDtDtBvA/\n46ux6ytAQ+A7Bd3AubvozwCr1Id6g66ae0blWYRRZmF8fDdX/SBjIUkv7u9A3NVQ\nXN9gsEvK9pdpfN4ZiflfGSLdhM1STHycLmhG6H5s7HklbukMRhQi+ejbSzm/wiw1\nipcxuKhSUIVNkTLusN5b+HE2gwF1fn0K0z5jWABy08huLgbaEKXJEx5/FKLZGJga\nfpIzAdf25kMTu3gggseaAmzyX3AtT1i8A8nqYfe8fnnVMkvud89kq5jErv/hlMC9\n49g5yWQR2jilYYM3j9BHDuB+Rs+YS5BCep1JnQ==\n-----END CERTIFICATE-----\n",
            "ca": "-----BEGIN CERTIFICATE-----\nMIIC6DCCAdCgAwIBAgIUdiBwE/CtaBXJl3MArjZen6Y8kigwDQYJKoZIhvcNAQEL\nBQAwIDELMAkGA1UEBhMCVVMxETAPBgNVBAMMCHdoYXRldmVyMB4XDTIzMDMyNDE4\nNDg1OVoXDTI0MDMyMzE4NDg1OVowPDELMAkGA1UEAwwCb2sxLTArBgNVBC0MJDEw\nMDdjNDBhLWUwYzMtNDVlOS05YTAxLTVlYjY0NWQ0ZmEyZDCCASIwDQYJKoZIhvcN\nAQEBBQADggEPADCCAQoCggEBANOnUl6JDlXpLMRr/PxgtfE/E5Yk6E/TkPkPL/Kk\ntUGjEi42XZDg9zn3U6cjTDYu+rfKY2jiitfsduW6DQIkEpz3AvbuCMbbgnFpcjsB\nYysLSMTmuz/AVPrfnea/tQTALcONCSy1VhAjGSr81ZRSMB4khl9StSauZrbkpJ1P\nshqkFSUyAi31mKrnXz0Es/v0Yi0FzAlgWrZ4u1Ld+Bo2Xz7oK4mHf7/93Jc+tEaM\nIqG6ocD0q8bjPp0tlSxftVADNUzWlZfM6fue5EXzOsKqyDrxYOSchfU9dNzKsaBX\nkxbHEeSUPJeYYj7aVPEfAs/tlUGsoXQvwWfRie8grp2BoLECAwEAATANBgkqhkiG\n9w0BAQsFAAOCAQEACZARBpHYH6Gr2a1ka0mCWfBmOZqfDVan9rsI5TCThoylmaXW\nquEiZ2LObI+5faPzxSBhr9TjJlQamsd4ywout7pHKN8ZGqrCMRJ1jJbUfobu1n2k\nUOsY4+jzV1IRBXJzj64fLal4QhUNv341lAer6Vz3cAyRk7CK89b/DEY0x+jVpyZT\n1osx9JtsOmkDTgvdStGzq5kPKWOfjwHkmKQaZXliCgqbhzcCERppp1s/sX6K7nIh\n4lWiEmzUSD3Hngk51KGWlpZszO5KQ4cSZ3HUt/prg+tt0ROC3pY61k+m5dDUa9M8\nRtMI6iTjzSj/UV8DiAx0yeM+bKoy4jGeXmaL3g==\n-----END CERTIFICATE-----\n",
            "chain": [
                "-----BEGIN CERTIFICATE-----\nMIIC6DCCAdCgAwIBAgIUW42TU9LSjEZLMCclWrvSwAsgRtcwDQYJKoZIhvcNAQEL\nBQAwIDELMAkGA1UEBhMCVVMxETAPBgNVBAMMCHdoYXRldmVyMB4XDTIzMDMyNDE4\nNDMxOVoXDTI0MDMyMzE4NDMxOVowPDELMAkGA1UEAwwCb2sxLTArBgNVBC0MJGUw\nNjVmMWI3LTE2OWEtNDE5YS1iNmQyLTc3OWJkOGM4NzIwNjCCASIwDQYJKoZIhvcN\nAQEBBQADggEPADCCAQoCggEBAK42ixoklDH5K5i1NxXo/AFACDa956pE5RA57wlC\nBfgUYaIDRmv7TUVJh6zoMZSD6wjSZl3QgP7UTTZeHbvs3QE9HUwEkH1Lo3a8vD3z\neqsE2vSnOkpWWnPbfxiQyrTm77/LAWBt7lRLRLdfL6WcucD3wsGqm58sWXM3HG0f\nSN7PHCZUFqU6MpkHw8DiKmht5hBgWG+Vq3Zw8MNaqpwb/NgST3yYdcZwb58G2FTS\nZvDSdUfRmD/mY7TpciYV8EFylXNNFkth8oGNLunR9adgZ+9IunfRKj1a7S5GSwXU\nAZDaojw+8k5i3ikztsWH11wAVCiLj/3euIqq95z8xGycnKcCAwEAATANBgkqhkiG\n9w0BAQsFAAOCAQEAWMvcaozgBrZ/MAxzTJmp5gZyLxmMNV6iT9dcqbwzDtDtBvA/\n46ux6ytAQ+A7Bd3AubvozwCr1Id6g66ae0blWYRRZmF8fDdX/SBjIUkv7u9A3NVQ\nXN9gsEvK9pdpfN4ZiflfGSLdhM1STHycLmhG6H5s7HklbukMRhQi+ejbSzm/wiw1\nipcxuKhSUIVNkTLusN5b+HE2gwF1fn0K0z5jWABy08huLgbaEKXJEx5/FKLZGJga\nfpIzAdf25kMTu3gggseaAmzyX3AtT1i8A8nqYfe8fnnVMkvud89kq5jErv/hlMC9\n49g5yWQR2jilYYM3j9BHDuB+Rs+YS5BCep1JnQ==\n-----END CERTIFICATE-----\n",
                "-----BEGIN CERTIFICATE-----\nMIIC6DCCAdCgAwIBAgIUdiBwE/CtaBXJl3MArjZen6Y8kigwDQYJKoZIhvcNAQEL\nBQAwIDELMAkGA1UEBhMCVVMxETAPBgNVBAMMCHdoYXRldmVyMB4XDTIzMDMyNDE4\nNDg1OVoXDTI0MDMyMzE4NDg1OVowPDELMAkGA1UEAwwCb2sxLTArBgNVBC0MJDEw\nMDdjNDBhLWUwYzMtNDVlOS05YTAxLTVlYjY0NWQ0ZmEyZDCCASIwDQYJKoZIhvcN\nAQEBBQADggEPADCCAQoCggEBANOnUl6JDlXpLMRr/PxgtfE/E5Yk6E/TkPkPL/Kk\ntUGjEi42XZDg9zn3U6cjTDYu+rfKY2jiitfsduW6DQIkEpz3AvbuCMbbgnFpcjsB\nYysLSMTmuz/AVPrfnea/tQTALcONCSy1VhAjGSr81ZRSMB4khl9StSauZrbkpJ1P\nshqkFSUyAi31mKrnXz0Es/v0Yi0FzAlgWrZ4u1Ld+Bo2Xz7oK4mHf7/93Jc+tEaM\nIqG6ocD0q8bjPp0tlSxftVADNUzWlZfM6fue5EXzOsKqyDrxYOSchfU9dNzKsaBX\nkxbHEeSUPJeYYj7aVPEfAs/tlUGsoXQvwWfRie8grp2BoLECAwEAATANBgkqhkiG\n9w0BAQsFAAOCAQEACZARBpHYH6Gr2a1ka0mCWfBmOZqfDVan9rsI5TCThoylmaXW\nquEiZ2LObI+5faPzxSBhr9TjJlQamsd4ywout7pHKN8ZGqrCMRJ1jJbUfobu1n2k\nUOsY4+jzV1IRBXJzj64fLal4QhUNv341lAer6Vz3cAyRk7CK89b/DEY0x+jVpyZT\n1osx9JtsOmkDTgvdStGzq5kPKWOfjwHkmKQaZXliCgqbhzcCERppp1s/sX6K7nIh\n4lWiEmzUSD3Hngk51KGWlpZszO5KQ4cSZ3HUt/prg+tt0ROC3pY61k+m5dDUa9M8\nRtMI6iTjzSj/UV8DiAx0yeM+bKoy4jGeXmaL3g==\n-----END CERTIFICATE-----\n",
            ],
            "version": 0,
        }
    ],
    "properties": {
        "certificate": {
            "$id": "#/properties/certificate",
            "type": "string",
            "title": "Public TLS certificate",
            "description": "Public TLS certificate",
        },
        "ca": {
            "$id": "#/properties/ca",
            "type": "string",
            "title": "CA public TLS certificate",
            "description": "CA Public TLS certificate",
        },
        "chain": {
            "$id": "#/properties/chain",
            "type": "array",
            "items": {"type": "string", "$id": "#/properties/chain/items"},
            "title": "CA public TLS certificate chain",
            "description": "CA public TLS certificate chain",
        },
        "version": {
            "$id": "#/properties/version",
            "type": "integer",
            "title": "Interface version",
            "minimum": 0,
            "description": "Highest supported version of this interface",
        },
    },
    "anyOf": [{"required": ["certificate"]}, {"required": ["ca"]}, {"required": ["chain"]}],
    "additionalProperties": True,
}


class CertificateAvailableEvent(EventBase):
    """Charm Event triggered when a TLS certificate is available."""

    def __init__(
        self,
        handle: Handle,
        certificate: str,
        ca: str,
        chain: List[str],
        relation_id: int,
    ):
        super().__init__(handle)
        self.certificate = certificate
        self.ca = ca
        self.chain = chain
        self.relation_id = relation_id

    def snapshot(self) -> dict:
        """Return snapshot."""
        return {
            "certificate": self.certificate,
            "ca": self.ca,
            "chain": self.chain,
            "relation_id": self.relation_id,
        }

    def restore(self, snapshot: dict):
        """Restores snapshot."""
        self.certificate = snapshot["certificate"]
        self.ca = snapshot["ca"]
        self.chain = snapshot["chain"]
        self.relation_id = snapshot["relation_id"]


class CertificateRemovedEvent(EventBase):
    """Charm Event triggered when a TLS certificate is removed."""

    def __init__(self, handle: Handle, relation_id: int):
        super().__init__(handle)
        self.relation_id = relation_id

    def snapshot(self) -> dict:
        """Return snapshot."""
        return {"relation_id": self.relation_id}

    def restore(self, snapshot: dict):
        """Restores snapshot."""
        self.relation_id = snapshot["relation_id"]


def _load_relation_data(raw_relation_data: Mapping[str, str]) -> dict:
    """Load relation data from the relation data bag.

    Args:
        raw_relation_data: Relation data from the databag

    Returns:
        dict: Relation data in dict format.
    """
    loaded_relation_data = {}
    for key in raw_relation_data:
        try:
            loaded_relation_data[key] = json.loads(raw_relation_data[key])
        except (json.decoder.JSONDecodeError, TypeError):
            loaded_relation_data[key] = raw_relation_data[key]
    return loaded_relation_data


class CertificateTransferRequirerCharmEvents(CharmEvents):
    """List of events that the Certificate Transfer requirer charm can leverage."""

    certificate_available = EventSource(CertificateAvailableEvent)
    certificate_removed = EventSource(CertificateRemovedEvent)


class CertificateTransferProvides(Object):
    """Certificate Transfer provider class."""

    def __init__(self, charm: CharmBase, relationship_name: str):
        super().__init__(charm, relationship_name)
        self.charm = charm
        self.relationship_name = relationship_name

    def set_certificate(
        self,
        certificate: str,
        ca: str,
        chain: List[str],
        relation_id: int,
    ) -> None:
        """Add certificates to relation data.

        Args:
            certificate (str): Certificate
            ca (str): CA Certificate
            chain (list): CA Chain
            relation_id (int): Juju relation ID

        Returns:
            None
        """
        relation = self.model.get_relation(
            relation_name=self.relationship_name,
            relation_id=relation_id,
        )
        if not relation:
            raise RuntimeError(
                f"No relation found with relation name {self.relationship_name} and "
                f"relation ID {relation_id}"
            )
        relation.data[self.model.unit]["certificate"] = certificate
        relation.data[self.model.unit]["ca"] = ca
        relation.data[self.model.unit]["chain"] = json.dumps(chain)
        relation.data[self.model.unit]["version"] = str(LIBAPI)

    def remove_certificate(self, relation_id: int) -> None:
        """Remove a given certificate from relation data.

        Args:
            relation_id (int): Relation ID

        Returns:
            None
        """
        relation = self.model.get_relation(
            relation_name=self.relationship_name,
            relation_id=relation_id,
        )
        if not relation:
            logger.warning(
                "Can't remove certificate - Non-existent relation '%s'", self.relationship_name
            )
            return
        unit_relation_data = relation.data[self.model.unit]
        certificate_removed = False
        if "certificate" in unit_relation_data:
            relation.data[self.model.unit].pop("certificate")
            certificate_removed = True
        if "ca" in unit_relation_data:
            relation.data[self.model.unit].pop("ca")
            certificate_removed = True
        if "chain" in unit_relation_data:
            relation.data[self.model.unit].pop("chain")
            certificate_removed = True

        if certificate_removed:
            logger.warning("Certificate removed from relation data")
        else:
            logger.warning("Can't remove certificate - No certificate in relation data")


class CertificateTransferRequires(Object):
    """TLS certificates requirer class to be instantiated by TLS certificates requirers."""

    on = CertificateTransferRequirerCharmEvents()  # type: ignore

    def __init__(
        self,
        charm: CharmBase,
        relationship_name: str,
    ):
        """Generates/use private key and observes relation changed event.

        Args:
            charm: Charm object
            relationship_name: Juju relation name
        """
        super().__init__(charm, relationship_name)
        self.relationship_name = relationship_name
        self.charm = charm
        self.framework.observe(
            charm.on[relationship_name].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[relationship_name].relation_broken, self._on_relation_broken
        )
        self.framework.observe(
            charm.on[relationship_name].relation_created, self._on_relation_created
        )

    @staticmethod
    def _relation_data_is_valid(relation_data: dict) -> bool:
        """Return whether relation data is valid based on json schema.

        Args:
            relation_data: Relation data in dict format.

        Returns:
            bool: Whether relation data is valid.
        """
        try:
            validate(instance=relation_data, schema=PROVIDER_JSON_SCHEMA)
            return True
        except exceptions.ValidationError:
            return False

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Emit certificate available event.

        Args:
            event: Juju event

        Returns:
            None
        """
        if not event.unit:
            logger.info("No remote unit in relation: %s", self.relationship_name)
            return
        remote_unit_relation_data = _load_relation_data(event.relation.data[event.unit])
        if not self._relation_data_is_valid(remote_unit_relation_data):
            logger.warning(
                "Provider relation data did not pass JSON Schema validation: %s",
                event.relation.data[event.unit],
            )
            return
        self.on.certificate_available.emit(
            certificate=remote_unit_relation_data.get("certificate"),
            ca=remote_unit_relation_data.get("ca"),
            chain=remote_unit_relation_data.get("chain"),
            relation_id=event.relation.id,
        )

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle relation broken event.

        Args:
            event: Juju event

        Returns:
            None
        """
        self.on.certificate_removed.emit(relation_id=event.relation.id)

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle relation created event.

        Args:
            event: Juju event

        Returns:
            None
        """
        if self.model.unit.is_leader():
            event.relation.data[self.model.app]["version"] = str(LIBAPI)

    def is_ready(self, relation: Relation) -> bool:
        """Check if the relation is ready by checking that it has valid relation data."""
        relation_data = _load_relation_data(relation.data[relation.units.pop()])
        if not self._relation_data_is_valid(relation_data):
            logger.warning("Provider relation data did not pass JSON Schema validation: ")
            return False
        return True
