#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""TLS Manager.

Responsible for managing the TLS configuration of the PostgreSQL instance.
"""

import logging
from datetime import timedelta

from charmlibs.interfaces.tls_certificates import (
    Certificate,
    PrivateKey,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope

from single_kernel_postgresql.config.exceptions import TlsError
from single_kernel_postgresql.config.literals import (
    APP_SCOPE,
)
from single_kernel_postgresql.config.statuses import GeneralStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.managers.base import BaseManager
from single_kernel_postgresql.utils.postgresql import PostgreSQL as PostgreSQLClient
from single_kernel_postgresql.workload.base import BaseWorkload

logger = logging.getLogger(__name__)


class TLSManager(BaseManager):
    """PostgreSQL TLS Manager.

    This manager is responsible for handling TLS configuration operations.
    """

    def __init__(self, state: CharmState, workload: BaseWorkload, client: PostgreSQLClient):
        super().__init__(state, workload, "tls_manager", client)

    def configure_internal_peer_ca(self) -> None:
        """Configure TLS internal peer CA."""
        if not self.state.get_secret(APP_SCOPE, "internal-ca"):
            self.generate_internal_peer_ca()

    def configure_internal_peer_cert(self) -> None:
        """Configure TLS internal peer certificate."""
        if not self.state.peer.internal_cert:
            self.generate_internal_peer_cert()

    def generate_internal_peer_cert(self) -> None:
        """Generate internal peer certificate using the tls lib."""
        if not (ca_key_secret := self.state.application.internal_ca_key):
            raise TlsError("No CA key content.")
        ca_key = PrivateKey.from_string(ca_key_secret)
        if not (ca_secret := self.state.application.internal_ca):
            raise TlsError("No CA cert content.")
        ca = Certificate.from_string(ca_secret)
        private_key = generate_private_key()
        csr = generate_csr(
            private_key,
            common_name=self.state.peer_common_name,
            sans_ip=frozenset(self.state.peer.peer_addresses),
            sans_dns=frozenset({
                *self.state.common_hosts,
                # IP address need to be part of the DNS SANs list due to
                # https://github.com/pgbackrest/pgbackrest/issues/1977.
                *self.state.peer.peer_addresses,
            }),
        )
        cert = generate_certificate(csr, ca, ca_key, validity=timedelta(days=7300))
        self.state.peer.internal_cert = str(cert)
        self.state.peer.internal_key = str(private_key)

        # self.charm.push_tls_files_to_workload()
        logger.info(
            "Internal peer certificate generated. Please use a proper TLS operator if possible."
        )

    def generate_internal_peer_ca(self) -> None:
        """Generate internal peer CA using the tls lib."""
        private_key = generate_private_key()
        ca = generate_ca(
            private_key,
            common_name=self.state.internal_peer_ca_common_name,
            validity=timedelta(days=7300),
        )
        logger.warning("Internal peer CA generated. Please use a proper TLS operator if possible.")
        self.state.set_secret(APP_SCOPE, "internal-ca-key", str(private_key))
        self.state.set_secret(APP_SCOPE, "internal-ca", str(ca))

    def get_statuses(
        self, scope: AdvancedStatusesScope, recompute: bool = False
    ) -> list[StatusObject]:
        """Compute the manager's statuses."""
        return [GeneralStatuses.ACTIVE_IDLE.value]
