# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from datetime import timedelta
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

        # Set up the initial relation and hooks.
        peer_rel_id = harness.add_relation(PEER, "postgresql")
        harness.add_relation_unit(peer_rel_id, "postgresql/0")
        harness.begin()
        yield harness
        harness.cleanup()


def test_generate_internal_peer_cert(harness):
    with (
        patch(
            "relations.tls.generate_private_key", return_value=sentinel.cert_key
        ) as _generate_private_key,
        patch("relations.tls.generate_csr", return_value=sentinel.cert_csr) as _generate_csr,
        patch(
            "relations.tls.generate_certificate", return_value=sentinel.cert
        ) as _generate_certificate,
        patch("relations.tls.PrivateKey") as _private_key,
        patch("relations.tls.Certificate") as _certificate,
        patch("charm.PostgresqlOperatorCharm.set_secret") as _set_secret,
        patch("charm.PostgresqlOperatorCharm.get_secret", return_value="secret value"),
        patch(
            "charm.PostgresqlOperatorCharm.push_tls_files_to_workload"
        ) as _push_tls_files_to_workload,
    ):
        _private_key.from_string.return_value = sentinel.ca_key
        _certificate.from_string.return_value = sentinel.ca_cert

        harness.charm.tls.generate_internal_peer_cert()

        _generate_csr.assert_called_once_with(
            sentinel.cert_key,
            common_name="postgresql-0",
            sans_ip=frozenset(),
            sans_dns=frozenset({"postgresql-0", "fqdn"}),
        )
        _generate_certificate.assert_called_once_with(
            sentinel.cert_csr, sentinel.ca_cert, sentinel.ca_key, validity=timedelta(days=7300)
        )
        assert _set_secret.call_count == 2
        _set_secret.assert_any_call("unit", "internal-key", str(sentinel.cert_key))
        _set_secret.assert_any_call("unit", "internal-cert", str(sentinel.cert))
        _push_tls_files_to_workload.assert_called_once_with()


def test_get_client_tls_files(harness):
    with patch(
        "relations.tls.TLSCertificatesRequiresV4.get_assigned_certificates"
    ) as _get_assigned_certificates:
        cert_mock = Mock()
        cert_mock.certificate = sentinel.certificate
        cert_mock.ca = sentinel.ca
        _get_assigned_certificates.return_value = ([cert_mock], sentinel.private_key)

        assert harness.charm.tls.get_client_tls_files() == (
            "sentinel.private_key",
            "sentinel.ca",
            "sentinel.certificate",
        )

        _get_assigned_certificates.return_value = (None, None)
        assert harness.charm.tls.get_client_tls_files() == (None, None, None)


def test_get_peer_tls_files(harness):
    with (
        patch(
            "relations.tls.TLSCertificatesRequiresV4.get_assigned_certificates"
        ) as _get_assigned_certificates,
        patch(
            "charm.PostgresqlOperatorCharm.get_secret", return_value=sentinel.secret
        ) as _get_secret,
    ):
        cert_mock = Mock()
        cert_mock.certificate = sentinel.certificate
        cert_mock.ca = sentinel.ca
        _get_assigned_certificates.return_value = ([cert_mock], sentinel.private_key)

        assert harness.charm.tls.get_peer_tls_files() == (
            "sentinel.private_key",
            "sentinel.ca",
            "sentinel.certificate",
        )
        assert not _get_secret.called

        _get_assigned_certificates.return_value = (None, None)
        assert harness.charm.tls.get_peer_tls_files() == (
            sentinel.secret,
            sentinel.secret,
            sentinel.secret,
        )
        assert _get_secret.call_count == 3
        _get_secret.assert_any_call("unit", "internal-key")
        _get_secret.assert_any_call("unit", "internal-cert")
        _get_secret.assert_any_call("app", "internal-ca")


def test_on_client_certificate_available(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.get_secret") as _get_secret,
        patch(
            "charm.PostgresqlOperatorCharm.push_tls_files_to_workload"
        ) as _push_tls_files_to_workload,
    ):
        # Defers if internal CA is not ready yet
        _get_secret.return_value = None
        event_mock = Mock()

        harness.charm.tls._on_certificate_available(event_mock)

        event_mock.defer.assert_called_once_with()
        assert not _push_tls_files_to_workload.called
        event_mock.reset_mock()

        # Defers if can't push
        _get_secret.return_value = sentinel.secret
        _push_tls_files_to_workload.return_value = False

        harness.charm.tls._on_certificate_available(event_mock)

        event_mock.defer.assert_called_once_with()
        _push_tls_files_to_workload.assert_called_once_with()
        event_mock.reset_mock()


def test_on_peer_certificate_available(harness):
    with (
        patch(
            "relations.tls.TLSCertificatesRequiresV4.get_assigned_certificates"
        ) as _get_assigned_certificates,
        patch("relations.tls.TLS._on_certificate_available") as _on_certificate_available,
        patch("charm.PostgresqlOperatorCharm.get_secret") as _get_secret,
        patch("charm.PostgresqlOperatorCharm.set_secret") as _set_secret,
    ):
        # Same ca
        cert_mock = Mock()
        cert_mock.ca = sentinel.ca
        _get_assigned_certificates.return_value = ([cert_mock], None)
        _get_secret.return_value = "sentinel.ca"
        event_mock = Mock()

        harness.charm.tls._on_peer_certificate_available(event_mock)

        _on_certificate_available.assert_called_once_with(event_mock)
        assert not _set_secret.called

        # Different ca
        _get_secret.return_value = sentinel.old_ca

        harness.charm.tls._on_peer_certificate_available(event_mock)

        assert _set_secret.call_count == 2
        _set_secret.assert_any_call("unit", "current-ca", "sentinel.ca")
        _set_secret.assert_any_call("unit", "old-ca", sentinel.old_ca)
