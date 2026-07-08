# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""TLS wiring tests for the single-kernel (lib) TLS handler.

The charm consumes the library TLS handler (single_kernel_postgresql.events.tls.TLS)
and TLS manager (single_kernel_postgresql.managers.tls.TLSManager) instead of its
own removed src/relations/tls.py. These tests exercise the lib-backed wiring:
state-backed cert storage via TLSManager, and the charm's reload bridge that calls
update_config after the handler stores+pushes certificates.
"""

from unittest.mock import Mock, PropertyMock, patch

import pytest
from ops.testing import Harness
from single_kernel_postgresql.config.literals import PEER_RELATION
from single_kernel_postgresql.events.tls import TLS
from single_kernel_postgresql.managers.tls import TLSManager

from charm import PostgresqlOperatorCharm


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    peer_rel_id = harness.add_relation(PEER_RELATION, "postgresql")
    harness.add_relation_unit(peer_rel_id, "postgresql/0")
    harness.begin()
    yield harness
    harness.cleanup()


def test_tls_handler_is_lib_backed(harness):
    """The charm wires the lib TLS handler + manager (not the removed relations.tls)."""
    charm = harness.charm
    assert isinstance(charm.tls, TLS)
    assert isinstance(charm.tls_manager, TLSManager)
    # The handler owns the operator client/peer requirers and the refresh event.
    assert hasattr(charm.tls, "client_certificate")
    assert hasattr(charm.tls, "peer_certificate")
    assert hasattr(charm.tls, "refresh_tls_certificates_event")
    # The removed method must not resurface anywhere.
    assert not hasattr(charm, "push_tls_files_to_workload")


def test_is_tls_enabled_reflects_tls_manager(harness):
    """is_tls_enabled is driven by TLSManager.get_client_tls_files(), not the handler."""
    with patch("charm.TLSManager.get_client_tls_files") as _get_client_tls_files:
        _get_client_tls_files.return_value = (None, None, None)
        assert harness.charm.is_tls_enabled is False

        _get_client_tls_files.return_value = ("key", "ca", "cert")
        assert harness.charm.is_tls_enabled is True


def test_reload_bridge_calls_update_config(harness):
    """_reload_tls_after_push delegates to update_config when internal-ca is present."""
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        harness.charm._reload_tls_after_push(Mock())
        _update_config.assert_called_once_with()


def test_reload_bridge_skips_update_config_without_internal_ca(harness):
    """_reload_tls_after_push is a no-op when internal-ca is absent (defer path).

    The lib TLS handler defers its push when the internal CA isn't present yet
    (no files written to disk).  The bridge must not call update_config in that
    case, or it would render ssl:on against TLS files that don't exist yet.
    """
    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        harness.charm._reload_tls_after_push(Mock())
        _update_config.assert_not_called()


def test_reload_bridge_defers_when_update_config_not_ready(harness):
    """A transient config-apply failure must defer (retry), not leave stale TLS state."""
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    event = Mock()
    with patch("charm.PostgresqlOperatorCharm.update_config", return_value=False):
        harness.charm._reload_tls_after_push(event)
    event.defer.assert_called_once()


def test_reload_bridge_defers_when_tls_files_not_yet_on_disk(harness):
    """ssl:on must not be rendered until the lib has pushed the cert files to disk."""
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")
    event = Mock()
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _uc,
        # is_tls_enabled -> True via the live-fetch getter
        patch("charm.TLSManager.get_client_tls_files", return_value=("K", "CA", "C")),
        patch.object(harness.charm.tls_manager, "client_tls_files_on_disk", return_value=False),
    ):
        harness.charm._reload_tls_after_push(event)
    event.defer.assert_called_once()
    _uc.assert_not_called()


def test_reload_bridge_defers_when_update_config_raises(harness):
    """A raising update_config must be caught and deferred, not fail the hook.

    The bridge mirrors the original charm's broad push-failure guard: a transient
    Patroni/render failure defers and retries rather than propagating out of the
    observer.
    """
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    event = Mock()
    with patch(
        "charm.PostgresqlOperatorCharm.update_config",
        side_effect=RuntimeError("patroni render failed"),
    ):
        # Must not raise.
        harness.charm._reload_tls_after_push(event)
    event.defer.assert_called_once_with()


def test_internal_cert_path_pushes_and_reloads(harness):
    """_check_and_update_internal_cert generates, pushes, and reloads on CN mismatch."""
    with (
        patch("charm.CharmState.unit_ip", new_callable=PropertyMock) as _unit_ip,
        patch(
            "charm.PostgresqlOperatorCharm.get_secret",
            return_value="-----BEGIN CERTIFICATE-----",
        ),
        patch("charm.load_pem_x509_certificate") as _load_cert,
        patch("charm.TLSManager.generate_internal_peer_cert") as _generate,
        patch("charm.TLSManager.push_tls_files") as _push,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
    ):
        _unit_ip.return_value = "1.2.3.4"
        # Make the cert CN differ from the unit IP to force regeneration.
        attr = Mock()
        attr.value = "9.9.9.9"
        _load_cert.return_value.subject.get_attributes_for_oid.return_value = [attr]

        harness.charm._check_and_update_internal_cert()

        _generate.assert_called_once_with()
        _push.assert_called_once_with()
        _update_config.assert_called_once_with()
