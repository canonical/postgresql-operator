# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import Mock, patch, sentinel

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER


@pytest.fixture(autouse=True)
def harness():
    with patch("relations.tls.socket") as _socket:
        _socket.getfqdn.return_value = "fqdn"
        harness = Harness(PostgresqlOperatorCharm)
        print(harness.model.juju_version)

        # Set up the initial relation and hooks.
        peer_rel_id = harness.add_relation(PEER, "postgresql")
        harness.add_relation_unit(peer_rel_id, "postgresql/0")
        harness.begin()
        yield harness
        harness.cleanup()


def test_get_tls_files(harness, only_with_juju_secrets):
    with patch(
        "relations.tls.TLSCertificatesRequiresV4.get_assigned_certificates"
    ) as _get_assigned_certificates:
        cert_mock = Mock()
        cert_mock.certificate = sentinel.certificate
        cert_mock.ca = sentinel.ca
        _get_assigned_certificates.return_value = ([cert_mock], sentinel.private_key)

        assert harness.charm.tls.get_tls_files() == (
            "sentinel.private_key",
            "sentinel.ca",
            "sentinel.certificate",
        )

        _get_assigned_certificates.return_value = (None, None)
        assert harness.charm.tls.get_tls_files() == (None, None, None)


def test_on_certificate_available(harness, only_with_juju_secrets):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.push_tls_files_to_workload"
        ) as _push_tls_files_to_workload,
    ):
        # Defers if can't push
        event_mock = Mock()
        _push_tls_files_to_workload.return_value = False

        harness.charm.tls._on_certificate_available(event_mock)

        event_mock.defer.assert_called_once_with()
        _push_tls_files_to_workload.assert_called_once_with()
        event_mock.reset_mock()
