# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import patch

import pytest
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.testing import Harness
from single_kernel_postgresql.config.literals import PEER

from charm import PostgresqlOperatorCharm
from relations.tls_transfer import TLS_TRANSFER_RELATION

SCOPE = "unit"


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    peer_rel_id = harness.add_relation(PEER, "postgresql")
    harness.add_relation_unit(peer_rel_id, "postgresql/0")
    harness.begin()
    yield harness
    harness.cleanup()


def relate_to_ca_certificates_operator(_harness):
    # Relate the charm to the send CA certificates operator.
    rel_id = _harness.add_relation(TLS_TRANSFER_RELATION, "ca-certificates-operator")
    _harness.add_relation_unit(rel_id, "ca-certificates-operator/0")
    return rel_id


def emit_ca_certificate_added_event(_harness, relation_id: int):
    _harness.charm.tls_transfer.certs_transfer.on.certificate_available.emit(
        relation_id=relation_id,
        certificate="test-cert",
        ca="test-ca",
        chain=["test-chain-ca-certificate", "test-chain-certificate"],
    )


def emit_ca_certificate_removed_event(_harness, relation_id: int):
    _harness.charm.tls_transfer.certs_transfer.on.certificate_removed.emit(
        relation_id=relation_id,
    )


def test_on_ca_certificate_added(harness):
    with (
        patch("ops.framework.EventBase.defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.push_ca_file_into_workload"
        ) as _push_ca_file_into_workload,
    ):
        rel_id = relate_to_ca_certificates_operator(harness)

        emit_ca_certificate_added_event(harness, rel_id)
        _push_ca_file_into_workload.assert_called_once()
        _defer.assert_not_called()

        _push_ca_file_into_workload.reset_mock()
        _push_ca_file_into_workload.side_effect = PebbleConnectionError

        emit_ca_certificate_added_event(harness, rel_id)
        _push_ca_file_into_workload.assert_called_once()
        _defer.assert_called_once()


def test_on_ca_certificate_removed(harness):
    with (
        patch("ops.framework.EventBase.defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.clean_ca_file_from_workload"
        ) as _clean_ca_file_from_workload,
    ):
        rel_id = relate_to_ca_certificates_operator(harness)

        emit_ca_certificate_removed_event(harness, rel_id)
        _clean_ca_file_from_workload.assert_called_once()
        _defer.assert_not_called()

        _clean_ca_file_from_workload.reset_mock()
        _clean_ca_file_from_workload.side_effect = PebbleConnectionError

        emit_ca_certificate_removed_event(harness, rel_id)
        _clean_ca_file_from_workload.assert_called_once()
        _defer.assert_called_once()
