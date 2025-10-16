# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from ops import Application
from tenacity import RetryError

from src.relations.async_replication import (
    READ_ONLY_MODE_BLOCKING_MESSAGE,
    REPLICATION_CONSUMER_RELATION,
    PostgreSQLAsyncReplication,
)


def create_mock_unit(name="unit"):
    unit = MagicMock()
    unit.name = name
    return unit


def test_on_secret_changed():
    # 1. relation is None
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    with (
        patch.object(
            PostgreSQLAsyncReplication, "_relation", new_callable=PropertyMock, return_value=None
        ),
        patch("logging.Logger.debug") as mock_debug,
    ):
        relation._on_secret_changed(mock_event)

        mock_debug.assert_called_once_with("Early exit on_secret_changed: No relation found.")
        mock_event.defer.assert_not_called()


def test__configure_primary_cluster():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._configure_primary_cluster(None, mock_event)
    assert result is False

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()
    mock_charm.unit.is_leader.return_value = False
    mock_charm.update_config = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation.is_primary_cluster = MagicMock(return_value=False)
    result = relation._configure_primary_cluster(mock_charm.app, mock_event)
    mock_charm.update_config.assert_called_once()
    assert result is True

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.update_config = MagicMock()
    mock_charm._patroni.get_standby_leader.return_value = True
    mock_charm._patroni.promote_standby_cluster = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation.is_primary_cluster = MagicMock(return_value=True)

    relation._update_primary_cluster_data = MagicMock()

    result = relation._configure_primary_cluster(mock_charm.app, mock_event)

    mock_charm.update_config.assert_called_once()
    relation._update_primary_cluster_data.assert_called_once()
    mock_charm._patroni.promote_standby_cluster()
    assert result is True

    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.update_config = MagicMock()
    mock_charm._patroni.get_standby_leader.return_value = None

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation.is_primary_cluster = MagicMock(return_value=True)

    relation._update_primary_cluster_data = MagicMock()

    result = relation._configure_primary_cluster(mock_charm.app, mock_event)

    mock_charm.update_config.assert_called_once()
    relation._update_primary_cluster_data.assert_called_once()
    assert result is True


def test__on_async_relation_departed():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_unit_data = {}
    mock_charm.unit_peer_data = mock_unit_data
    mock_event.departing_unit = MagicMock()
    mock_charm.unit = mock_event.departing_unit

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._on_async_relation_departed(mock_event)
    assert result is None
    assert mock_unit_data == {"departing": "True"}


def test_on_async_relation_joined():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_unit_data = {}
    mock_charm.unit_peer_data = mock_unit_data

    mock_charm._unit_ip = "10.0.0.1"

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_highest_promoted_cluster_counter_value = MagicMock(return_value="1")

    result = relation._on_async_relation_joined(mock_event)

    assert result is None

    assert mock_unit_data == {"unit-promoted-cluster-counter": "1"}

    relation._get_highest_promoted_cluster_counter_value.assert_called_once()


def test_on_create_replication():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_application = MagicMock(spec=Application)
    relation._get_primary_cluster = MagicMock(return_value=mock_application)

    result = relation._on_create_replication(mock_event)

    assert result is None
    mock_event.fail.assert_called_once_with("There is already a replication set up.")

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_primary_cluster = MagicMock(return_value=None)

    mock_relation = MagicMock()
    mock_relation.name = REPLICATION_CONSUMER_RELATION
    type(relation)._relation = PropertyMock(return_value=mock_relation)

    result = relation._on_create_replication(mock_event)

    assert result is None
    mock_event.fail.assert_called_once_with(
        "This action must be run in the cluster where the offer was created."
    )
    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_primary_cluster = MagicMock(return_value=None)

    relation._handle_replication_change = MagicMock(return_value=True)

    mock_relation = MagicMock()
    mock_relation.name = "Something"
    type(relation)._relation = PropertyMock(return_value=mock_relation)

    result = relation._on_create_replication(mock_event)

    assert result is None

    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_primary_cluster = MagicMock(return_value=None)

    relation._handle_replication_change = MagicMock(return_value=False)

    mock_relation = MagicMock()
    mock_relation.name = "Something"
    type(relation)._relation = PropertyMock(return_value=mock_relation)

    result = relation._on_create_replication(mock_event)

    assert result is None


def test_promote_to_primary():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()
    mock_relation.status = MagicMock()
    mock_relation.status.message = "Something"

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._get_primary_cluster = MagicMock(return_value=None)

    type(relation).app = PropertyMock(return_value=mock_relation)
    result = relation.promote_to_primary(mock_event)
    assert result is None

    mock_event.fail.assert_called_once_with(
        "No primary cluster found. Run `create-replication` action in the cluster where the offer was created."
    )

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()
    mock_app = MagicMock(spec=Application)
    mock_relation.status = MagicMock()
    mock_relation.status.message = READ_ONLY_MODE_BLOCKING_MESSAGE

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._get_primary_cluster = MagicMock(return_value=None)

    type(relation).app = PropertyMock(return_value=mock_app)
    relation._handle_replication_change = MagicMock(return_value=False)

    result = relation.promote_to_primary(mock_event)

    assert result is None


def test__configure_standby_cluster():
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = MagicMock()
    relation._relation.name = REPLICATION_CONSUMER_RELATION
    relation._update_internal_secret = MagicMock(return_value=False)

    result = relation._configure_standby_cluster(mock_event)

    assert result is False

    mock_event.defer.assert_called_once()

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = MagicMock()
    relation._relation.name = "something_else"
    relation._update_internal_secret = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=(None, 2))

    with pytest.raises(Exception) as exc_info:
        relation._configure_standby_cluster(mock_event)

    assert str(exc_info.value) == "2"

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = MagicMock()
    relation._relation.name = "some_relation"
    relation._relation.app = "remote-app"
    relation._relation.data = {relation._relation.app: {"system-id": "123"}}

    relation._update_internal_secret = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=("456", None))
    relation.charm = MagicMock()
    relation.charm.app_peer_data = {}

    with patch("subprocess.check_call") as mock_check_call:
        result = relation._configure_standby_cluster(mock_event)

        assert result is True
        mock_check_call.assert_called_once()


def test_wait_for_standby_leader():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_charm._patroni.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = False
    mock_charm._patroni.is_member_isolated = True
    mock_charm._patroni.restart_patroni = MagicMock()

    result = relation._wait_for_standby_leader(mock_event)
    assert result is True
    mock_charm._patroni.restart_patroni.assert_called_once()
    mock_event.defer.assert_called_once()

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_charm._patroni.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = False
    mock_charm._patroni.is_member_isolated = False

    result = relation._wait_for_standby_leader(mock_event)
    assert result is True
    mock_event.defer.assert_called_once()

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    mock_charm._patroni.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = True

    result = relation._wait_for_standby_leader(mock_event)
    assert result is False


def test_get_partner_addresses():
    mock_charm = MagicMock()

    mock_charm._peer_members_ips = ["str"]
    mock_charm.app = MagicMock()
    mock_charm.unit = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm._peers = MagicMock()
    mock_charm._peers.data = {mock_charm.unit: {}}

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._get_primary_cluster = MagicMock(return_value=None)
    relation._get_highest_promoted_cluster_counter_value = MagicMock(return_value=None)

    result = relation.get_partner_addresses()

    assert result == ["str"]


def test_handle_replication_change():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._can_promote_cluster = MagicMock(return_value=False)
    result = relation._handle_replication_change(mock_event)
    assert result is False
    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._can_promote_cluster = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=(12345, "some error"))
    result = relation._handle_replication_change(mock_event)
    assert result is False

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()

    mock_unit1 = MagicMock()
    mock_unit2 = MagicMock()
    mock_relation.units = [mock_unit1, mock_unit2]
    mock_relation.data = {
        mock_unit1: {"unit-address": "10.0.0.1"},
        mock_unit2: {"unit-address": "10.0.0.2"},
        mock_charm.app: {},
    }

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = mock_relation
    relation._can_promote_cluster = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=(12345, None))
    relation._get_highest_promoted_cluster_counter_value = MagicMock(return_value="1")
    relation._update_primary_cluster_data = MagicMock()
    relation._re_emit_async_relation_changed_event = MagicMock()

    result = relation._handle_replication_change(mock_event)

    assert result is True
    relation._can_promote_cluster.assert_called_once_with(mock_event)
    relation.get_system_identifier.assert_called_once()
    relation._get_highest_promoted_cluster_counter_value.assert_called_once()
    relation._update_primary_cluster_data.assert_called_once_with(2, 12345)
    relation._re_emit_async_relation_changed_event.assert_called_once()
    mock_event.fail.assert_not_called()


def test_handle_forceful_promotion():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = True
    relation = PostgreSQLAsyncReplication(mock_charm)
    result = relation._handle_forceful_promotion(mock_event)

    assert result is True
    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = False

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._relation = MagicMock()
    relation._relation.app = MagicMock()
    relation._relation.app.name = "test-app"

    relation.get_all_primary_cluster_endpoints = MagicMock(return_value=[1, 2, 3])

    mock_charm._patroni.get_primary.side_effect = RetryError("timeout")

    result = relation._handle_forceful_promotion(mock_event)

    mock_event.fail.assert_called_once_with(
        "test-app isn't reachable. Pass `force=true` to promote anyway."
    )
    assert result is False
    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = False

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._relation = MagicMock()
    relation._relation.app = MagicMock()
    relation._relation.app.name = "test-app"

    relation.get_all_primary_cluster_endpoints = MagicMock(return_value=[1, 2, 3])

    mock_charm._patroni.get_primary.side_effect = None

    result = relation._handle_forceful_promotion(mock_event)

    assert result is True
    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = False

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._relation = MagicMock()
    relation._relation.app = MagicMock()
    relation._relation.app.name = "test-app"

    relation.get_all_primary_cluster_endpoints = MagicMock(return_value=[])

    mock_charm._patroni.get_primary.side_effect = None

    result = relation._handle_forceful_promotion(mock_event)

    assert result is True


def test_on_async_relation_broken():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._peers = True

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._on_async_relation_broken(mock_event)

    assert result is None
    # 2.
    mock_charm = MagicMock()
    mock_charm._peers = MagicMock()
    mock_charm.is_unit_departing = False
    mock_charm._patroni.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = True
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._on_async_relation_broken(mock_event)

    assert mock_charm.update_config.called
