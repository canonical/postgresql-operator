# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from sys import modules
from unittest.mock import Mock

from ops.pebble import ConnectionError as PebbleConnectionError
from single_kernel_postgresql.config.literals import PEER_RELATION

# Need to mock the module before importing the tested module
modules["charms.certificate_transfer_interface.v0.certificate_transfer"] = Mock()

from single_kernel_postgresql.events.tls_transfer import TLSTransfer  # noqa: E402

SCOPE = "unit"


def test_on_ca_certificate_added():
    mock_charm = Mock()
    mock_charm.model.get_relation.return_value.app.name = "testname"
    mock_event = Mock()
    tls_transfer = TLSTransfer(mock_charm, PEER_RELATION)

    # Happy scenario
    tls_transfer._on_certificate_available(mock_event)

    mock_charm.push_ca_file_into_workload.assert_called_once_with("ca-testname")
    mock_event.defer.assert_not_called()
    mock_charm.reset_mock()
    mock_event.reset_mock()

    # Connection error
    mock_charm.push_ca_file_into_workload.side_effect = PebbleConnectionError

    tls_transfer._on_certificate_available(mock_event)

    mock_charm.push_ca_file_into_workload.assert_called_once_with("ca-testname")
    mock_event.defer.assert_called_once_with()
    mock_charm.reset_mock()
    mock_event.reset_mock()

    # Failed to push
    mock_charm.push_ca_file_into_workload.side_effect = None
    mock_charm.push_ca_file_into_workload.return_value = False

    tls_transfer._on_certificate_available(mock_event)

    mock_charm.push_ca_file_into_workload.assert_called_once_with("ca-testname")
    mock_event.defer.assert_called_once_with()
    mock_charm.reset_mock()
    mock_event.reset_mock()

    # No relation
    mock_charm.model.get_relation.return_value = None

    tls_transfer._on_certificate_available(mock_event)

    mock_charm.push_ca_file_into_workload.assert_not_called()
    mock_event.defer.assert_not_called()
    mock_charm.reset_mock()
    mock_event.reset_mock()


def test_on_ca_certificate_removed():
    mock_charm = Mock()
    mock_charm.model.get_relation.return_value.app.name = "testname"
    mock_event = Mock()
    tls_transfer = TLSTransfer(mock_charm, PEER_RELATION)

    # Happy scenario
    tls_transfer._on_certificate_removed(mock_event)

    mock_charm.clean_ca_file_from_workload.assert_called_once_with("ca-testname")
    mock_event.defer.assert_not_called()
    mock_charm.reset_mock()
    mock_event.reset_mock()

    # Connection error
    mock_charm.clean_ca_file_from_workload.side_effect = PebbleConnectionError

    tls_transfer._on_certificate_removed(mock_event)

    mock_charm.clean_ca_file_from_workload.assert_called_once_with("ca-testname")
    mock_event.defer.assert_called_once_with()
    mock_charm.reset_mock()
    mock_event.reset_mock()

    # Failed to push
    mock_charm.clean_ca_file_from_workload.side_effect = None
    mock_charm.clean_ca_file_from_workload.return_value = False

    tls_transfer._on_certificate_removed(mock_event)

    mock_charm.clean_ca_file_from_workload.assert_called_once_with("ca-testname")
    mock_event.defer.assert_called_once_with()
    mock_charm.reset_mock()
    mock_event.reset_mock()

    # No relation
    mock_charm.model.get_relation.return_value = None

    tls_transfer._on_certificate_removed(mock_event)

    mock_charm.clean_ca_file_from_workload.assert_not_called()
    mock_event.defer.assert_not_called()
    mock_charm.reset_mock()
    mock_event.reset_mock()
