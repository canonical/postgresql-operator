# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from ops import Application
from ops.model import WaitingStatus
from tenacity import RetryError

from src.relations.async_replication import (
    READ_ONLY_MODE_BLOCKING_MESSAGE,
    REPLICATION_CONSUMER_RELATION,
    NotReadyError,
    PostgreSQLAsyncReplication,
    StandbyClusterAlreadyPromotedError,
)


def create_mock_unit(name="unit"):
    unit = MagicMock()
    unit.name = name
    return unit


def test_can_promote_cluster():
    # 1. Test when cluster is not initialized
    mock_charm = MagicMock()
    mock_event = MagicMock()
    type(mock_charm).is_cluster_initialised = PropertyMock(return_value=False)

    relation = PostgreSQLAsyncReplication(mock_charm)

    assert relation._can_promote_cluster(mock_event) is False
    mock_event.fail.assert_called_with("Cluster not initialised yet.")

    # 2. Test when cluster is initialized but no relation exists
    mock_charm = MagicMock()
    mock_event = MagicMock()
    type(mock_charm).is_cluster_initialised = PropertyMock(return_value=True)

    mock_peers_data = MagicMock()
    mock_peers_data.update = MagicMock()

    with patch.multiple(
        PostgreSQLAsyncReplication,
        _relation=None,
        _get_primary_cluster=MagicMock(),
        _set_app_status=MagicMock(),
        _handle_forceful_promotion=MagicMock(return_value=False),
    ):
        mock_charm._patroni = MagicMock()
        mock_charm._patroni.get_standby_leader.return_value = "postgresql/1"
        mock_charm._patroni.promote_standby_cluster.return_value = None
        mock_charm.app.status.message = READ_ONLY_MODE_BLOCKING_MESSAGE
        mock_charm._peers = MagicMock()
        mock_charm._peers.data = {mock_charm.app: mock_peers_data}
        mock_charm._set_primary_status_message = MagicMock()

        relation = PostgreSQLAsyncReplication(mock_charm)
        assert relation._can_promote_cluster(mock_event) is False

        mock_peers_data.update.assert_called_once_with({"promoted-cluster-counter": ""})
        relation._set_app_status.assert_called_once()
        mock_charm._set_primary_status_message.assert_called_once()

        # 2b. Test when standby leader exists but promotion fails
        mock_charm._patroni.promote_standby_cluster.side_effect = (
            StandbyClusterAlreadyPromotedError("Already promoted")
        )
        relation = PostgreSQLAsyncReplication(mock_charm)
        assert relation._can_promote_cluster(mock_event) is False
        mock_event.fail.assert_called_with("Already promoted")

        # 2c. Test when no standby leader exists
        mock_charm._patroni.get_standby_leader.return_value = None
        relation = PostgreSQLAsyncReplication(mock_charm)
        assert relation._can_promote_cluster(mock_event) is False
        mock_event.fail.assert_called_with("No relation and no standby leader found.")

    # 3. Test normal case with relation exists
    mock_charm = MagicMock()
    mock_event = MagicMock()
    type(mock_charm).is_cluster_initialised = PropertyMock(return_value=True)

    with (
        patch.object(PostgreSQLAsyncReplication, "_get_primary_cluster") as mock_get_primary,
        patch.object(
            PostgreSQLAsyncReplication, "_relation", new_callable=PropertyMock
        ) as mock_relation,
        patch.object(PostgreSQLAsyncReplication, "_handle_forceful_promotion", return_value=True),
    ):
        mock_relation.return_value = MagicMock()
        mock_get_primary.return_value = (MagicMock(), "1")
        relation = PostgreSQLAsyncReplication(mock_charm)
        assert relation._can_promote_cluster(mock_event) is True

    # 4.
    mock_app = MagicMock()
    mock_charm.app = mock_app

    with patch.object(PostgreSQLAsyncReplication, "_get_primary_cluster") as mock_get_primary:
        mock_get_primary.return_value = mock_app

        relation = PostgreSQLAsyncReplication(mock_charm)
        result = relation._can_promote_cluster(mock_event)

        assert result is False
        mock_event.fail.assert_called_with("This cluster is already the primary cluster.")


def test_handle_database_start():
    # 1. Test when database is started (member_started = True) and all units ready
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._patroni.member_started = True
    mock_charm.unit.is_leader.return_value = True

    mock_unit1 = create_mock_unit()
    mock_unit2 = create_mock_unit()
    mock_charm.unit = create_mock_unit()
    mock_charm.app = MagicMock()

    mock_peers_data = {
        mock_charm.unit: MagicMock(),
        mock_unit1: MagicMock(),
        mock_unit2: MagicMock(),
        mock_charm.app: MagicMock(),
    }
    mock_charm._peers = MagicMock()
    mock_charm._peers.data = mock_peers_data
    mock_charm._peers.units = [mock_unit1, mock_unit2]

    with (
        patch.object(
            PostgreSQLAsyncReplication,
            "_get_highest_promoted_cluster_counter_value",
            return_value="1",
        ),
        patch.object(
            PostgreSQLAsyncReplication, "_is_following_promoted_cluster", return_value=False
        ),
    ):
        for unit in [mock_unit1, mock_unit2, mock_charm.unit]:
            mock_peers_data[unit].get.return_value = "1"

        relation = PostgreSQLAsyncReplication(mock_charm)
        relation._handle_database_start(mock_event)

        mock_peers_data[mock_charm.unit].update.assert_any_call({"stopped": ""})
        mock_peers_data[mock_charm.unit].update.assert_any_call({
            "unit-promoted-cluster-counter": "1"
        })
        mock_charm.update_config.assert_called_once()
        mock_peers_data[mock_charm.app].update.assert_called_once_with({
            "cluster_initialised": "True"
        })
        mock_charm._set_primary_status_message.assert_called_once()

    # 2. Test when not all units are ready (leader case)
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._patroni.member_started = True
    mock_charm.unit.is_leader.return_value = True

    mock_unit1 = create_mock_unit()
    mock_unit2 = create_mock_unit()
    mock_charm.unit = create_mock_unit()
    mock_charm.app = MagicMock()

    mock_peers_data = {
        mock_charm.unit: MagicMock(),
        mock_unit1: MagicMock(),
        mock_unit2: MagicMock(),
        mock_charm.app: MagicMock(),
    }
    mock_charm._peers = MagicMock()
    mock_charm._peers.data = mock_peers_data
    mock_charm._peers.units = [mock_unit1, mock_unit2]

    with (
        patch.object(
            PostgreSQLAsyncReplication,
            "_get_highest_promoted_cluster_counter_value",
            return_value="1",
        ),
        patch.object(
            PostgreSQLAsyncReplication, "_is_following_promoted_cluster", return_value=True
        ),
    ):
        mock_peers_data[mock_charm.unit].get.return_value = "1"
        mock_peers_data[mock_unit1].get.return_value = "1"
        mock_peers_data[mock_unit2].get.return_value = "0"

        relation = PostgreSQLAsyncReplication(mock_charm)
        relation._handle_database_start(mock_event)

        assert isinstance(mock_charm.unit.status, WaitingStatus)
        mock_event.defer.assert_called_once()

    # 3. Test when database is not started (non-leader case)
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._patroni.member_started = False
    mock_charm.unit.is_leader.return_value = False

    with (
        patch.object(PostgreSQLAsyncReplication, "_get_highest_promoted_cluster_counter_value"),
        patch("src.relations.async_replication.contextlib.suppress") as mock_suppress,
    ):
        mock_suppress.return_value.__enter__.return_value = None
        mock_charm._patroni.reload_patroni_configuration.side_effect = NotReadyError()

        relation = PostgreSQLAsyncReplication(mock_charm)
        relation._handle_database_start(mock_event)

        mock_charm._patroni.reload_patroni_configuration.assert_called_once()
        assert isinstance(mock_charm.unit.status, WaitingStatus)
        mock_event.defer.assert_called_once()

    # 4. Test when database is starting (leader case)
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._patroni.member_started = False
    mock_charm.unit.is_leader.return_value = True

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._handle_database_start(mock_event)

    # Verify waiting status and deferral
    assert isinstance(mock_charm.unit.status, WaitingStatus)
    mock_event.defer.assert_called_once()


def test_on_async_relation_changed():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.unit = create_mock_unit("leader")
    mock_charm.app = MagicMock()
    mock_unit1 = create_mock_unit("unit1")
    mock_unit2 = create_mock_unit("unit2")
    mock_charm._peers.units = [mock_unit1, mock_unit2]
    mock_charm._peers.data = {
        mock_charm.unit: {"stopped": "1"},
        mock_unit1: {"unit-promoted-cluster-counter": "5"},
        mock_unit2: {"unit-promoted-cluster-counter": "5"},
        mock_charm.app: {"promoted-cluster-counter": "5"},
    }
    mock_charm.is_unit_stopped = True

    relation = PostgreSQLAsyncReplication(mock_charm)

    with (
        patch.object(relation, "_get_primary_cluster", return_value=None),
        patch.object(relation, "_set_app_status") as mock_status,
    ):
        relation._on_async_relation_changed(mock_event)
        mock_status.assert_called_once()
        mock_event.defer.assert_not_called()

    with (
        patch.object(relation, "_get_primary_cluster", return_value="clusterX"),
        patch.object(relation, "_configure_primary_cluster", return_value=True),
    ):
        relation._on_async_relation_changed(mock_event)
        mock_event.defer.assert_not_called()

    mock_charm.unit.is_leader.return_value = False
    with (
        patch.object(relation, "_get_primary_cluster", return_value="clusterX"),
        patch.object(relation, "_configure_primary_cluster", return_value=False),
        patch.object(relation, "_is_following_promoted_cluster", return_value=True),
    ):
        relation._on_async_relation_changed(mock_event)
        mock_event.defer.assert_not_called()

    mock_charm.unit.is_leader.return_value = True
    mock_charm.is_unit_stopped = False
    with (
        patch.object(relation, "_get_primary_cluster", return_value="clusterX"),
        patch.object(relation, "_configure_primary_cluster", return_value=False),
        patch.object(relation, "_is_following_promoted_cluster", return_value=False),
        patch.object(relation, "_stop_database", return_value=True),
        patch.object(relation, "_get_highest_promoted_cluster_counter_value", return_value="5"),
    ):
        relation._on_async_relation_changed(mock_event)
        assert isinstance(mock_charm.unit.status, WaitingStatus)
        mock_event.defer.assert_called()

    mock_charm.is_unit_stopped = True
    with (
        patch.object(relation, "_get_primary_cluster", return_value="clusterX"),
        patch.object(relation, "_configure_primary_cluster", return_value=False),
        patch.object(relation, "_is_following_promoted_cluster", return_value=False),
        patch.object(relation, "_stop_database", return_value=True),
        patch.object(relation, "_get_highest_promoted_cluster_counter_value", return_value="5"),
        patch.object(relation, "_wait_for_standby_leader", return_value=True),
    ):
        relation._on_async_relation_changed(mock_event)

        mock_charm._patroni.start_patroni.assert_not_called()

    with (
        patch.object(relation, "_get_primary_cluster", return_value="clusterX"),
        patch.object(relation, "_configure_primary_cluster", return_value=False),
        patch.object(relation, "_is_following_promoted_cluster", return_value=False),
        patch.object(relation, "_stop_database", return_value=True),
        patch.object(relation, "_get_highest_promoted_cluster_counter_value", return_value="5"),
        patch.object(relation, "_wait_for_standby_leader", return_value=False),
        patch.object(mock_charm._patroni, "start_patroni", return_value=True),
        patch.object(relation, "_handle_database_start") as mock_handle_start,
    ):
        relation._on_async_relation_changed(mock_event)
        mock_charm.update_config.assert_called_once()
        mock_handle_start.assert_called_once_with(mock_event)


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


def test_stop_database():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.is_unit_stopped = False
    mock_charm.unit.is_leader.return_value = False
    mock_charm._patroni.stop_patroni.return_value = True

    mock_unit = MagicMock()
    mock_app = MagicMock()
    mock_charm.unit = mock_unit
    mock_charm.app = mock_app
    mock_charm._peers.data = {mock_app: {}, mock_unit: {}}

    relation = PostgreSQLAsyncReplication(mock_charm)

    # 1. Test early exit when following promoted cluster
    with (
        patch.object(
            PostgreSQLAsyncReplication, "_is_following_promoted_cluster", return_value=True
        ),
        patch("os.path.exists", return_value=True),
    ):
        result = relation._stop_database(mock_event)
        assert result is True
        mock_charm._patroni.stop_patroni.assert_not_called()

    # 2. Test non-leader with no data path
    mock_charm._patroni.stop_patroni.return_value = True
    with (
        patch.object(
            PostgreSQLAsyncReplication, "_is_following_promoted_cluster", return_value=False
        ),
        patch("os.path.exists", return_value=False),
    ):
        mock_charm.unit.is_leader.return_value = False
        result = relation._stop_database(mock_event)
        assert result is False
        mock_charm._patroni.stop_patroni.assert_not_called()

    # 3. Test leader unit behavior
    with (
        patch.object(
            PostgreSQLAsyncReplication, "_is_following_promoted_cluster", return_value=False
        ),
        patch("os.path.exists", return_value=True),
        patch.object(PostgreSQLAsyncReplication, "_configure_standby_cluster", return_value=True),
        patch.object(PostgreSQLAsyncReplication, "_reinitialise_pgdata"),
        patch("shutil.rmtree"),
        patch("pathlib.Path") as mock_path,
    ):
        mock_path_instance = MagicMock()
        mock_path.return_value = mock_path_instance
        mock_path_instance.exists.return_value = True
        mock_path_instance.is_dir.return_value = True

        mock_charm.unit.is_leader.return_value = True
        result = relation._stop_database(mock_event)
        assert result is True
        mock_charm._patroni.stop_patroni.assert_called_once()
        assert mock_charm._peers.data[mock_app].get("cluster_initialised") == ""
        assert mock_charm._peers.data[mock_unit].get("stopped") == "True"


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
    mock_peers = MagicMock()
    mock_unit_data = {}
    mock_event.departing_unit = MagicMock()
    mock_charm.unit = mock_event.departing_unit
    mock_charm._peers = mock_peers
    mock_peers.data = {mock_charm.unit: mock_unit_data}

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._on_async_relation_departed(mock_event)
    assert result is None
    assert mock_unit_data == {"departing": "True"}


def test_on_async_relation_joined():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_peers = MagicMock()
    mock_unit_data = {}

    mock_charm._unit_ip = "10.0.0.1"
    mock_charm._peers = mock_peers
    mock_peers.data = {mock_charm.unit: mock_unit_data}

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
