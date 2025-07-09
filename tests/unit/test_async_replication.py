import contextlib
from unittest.mock import MagicMock, PropertyMock, patch
import pytest
import os
from tenacity import RetryError
from ops.model import WaitingStatus
from src.relations.async_replication import (
    PostgreSQLAsyncReplication,
    NotReadyError,
    READ_ONLY_MODE_BLOCKING_MESSAGE,
    StandbyClusterAlreadyPromotedError
)

REPLICATION_OFFER_RELATION = "replication-offer"
REPLICATION_CONSUMER_RELATION = "replication-consumer"
PEER = "peer"
SECRET_LABEL = "secret-label"
SNAP_CURRENT_PATH = "/var/snap/charmed-postgresql/current"

SNAP_CONF_PATH = f"{SNAP_CURRENT_PATH}/etc"
PATRONI_CONF_PATH = f"{SNAP_CONF_PATH}/patroni"

def create_mock_unit(name="unit"):
    unit = MagicMock()
    unit.name = name
    return unit


def test_can_promote_cluster():
    """Tests all conditions in _can_promote_cluster"""
    
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
    
    # Create fresh mocks for this test case
    mock_peers_data = MagicMock()
    mock_peers_data.update = MagicMock()
    
    with patch.multiple(PostgreSQLAsyncReplication,
                      _relation=None,
                      _get_primary_cluster=MagicMock(),
                      _set_app_status=MagicMock(),
                      _handle_forceful_promotion=MagicMock(return_value=False)):
        
        # Setup test-specific conditions
        mock_charm._patroni = MagicMock()
        mock_charm._patroni.get_standby_leader.return_value = "standby-leader"
        mock_charm._patroni.promote_standby_cluster.return_value = True
        mock_charm.app.status.message = READ_ONLY_MODE_BLOCKING_MESSAGE
        mock_charm._peers = MagicMock()
        mock_charm._peers.data = {mock_charm.app: mock_peers_data}
        mock_charm._set_primary_status_message = MagicMock()
        
        relation = PostgreSQLAsyncReplication(mock_charm)
        assert relation._can_promote_cluster(mock_event) is False
        
        # Verify only the expected calls for this test case
        mock_peers_data.update.assert_called_once_with({
            "promoted-cluster-counter": ""
        })
        relation._set_app_status.assert_called_once()
        mock_charm._set_primary_status_message.assert_called_once()

        # 2b. Test when standby leader exists but promotion fails
        mock_charm._patroni.promote_standby_cluster.side_effect = StandbyClusterAlreadyPromotedError("Already promoted")
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
    
    with patch.object(PostgreSQLAsyncReplication, '_get_primary_cluster') as mock_get_primary:
        # Mock that relation exists
        with patch.object(PostgreSQLAsyncReplication, '_relation', new_callable=PropertyMock) as mock_relation:
            mock_relation.return_value = MagicMock()  # Simulate existing relation
            
            mock_get_primary.return_value = (MagicMock(), "1")
            with patch.object(PostgreSQLAsyncReplication, '_handle_forceful_promotion', return_value=True):
                relation = PostgreSQLAsyncReplication(mock_charm)
                assert relation._can_promote_cluster(mock_event) is True

def test_handle_database_start():
    """Tests all conditions in _handle_database_start"""

    # 1. Test when database is started (member_started = True) and all units ready
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._patroni.member_started = True
    mock_charm.unit.is_leader.return_value = True
    
    # Create mock units
    mock_unit1 = create_mock_unit()
    mock_unit2 = create_mock_unit()
    mock_charm.unit = create_mock_unit()
    mock_charm.app = MagicMock()
    
    # Setup peers data structure with proper keys
    mock_peers_data = {
        mock_charm.unit: MagicMock(),
        mock_unit1: MagicMock(),
        mock_unit2: MagicMock(),
        mock_charm.app: MagicMock()
    }
    mock_charm._peers = MagicMock()
    mock_charm._peers.data = mock_peers_data
    mock_charm._peers.units = [mock_unit1, mock_unit2]
    
    with patch.object(PostgreSQLAsyncReplication, '_get_highest_promoted_cluster_counter_value', return_value="1"), \
         patch.object(PostgreSQLAsyncReplication, '_is_following_promoted_cluster', return_value=False):
        
        # Configure all units to have matching counter values
        for unit in [mock_unit1, mock_unit2, mock_charm.unit]:
            mock_peers_data[unit].get.return_value = "1"
        
        relation = PostgreSQLAsyncReplication(mock_charm)
        relation._handle_database_start(mock_event)
        
        # Verify updates when all units are ready
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
        mock_charm.app: MagicMock()
    }
    mock_charm._peers = MagicMock()
    mock_charm._peers.data = mock_peers_data
    mock_charm._peers.units = [mock_unit1, mock_unit2]
    
    with patch.object(PostgreSQLAsyncReplication, '_get_highest_promoted_cluster_counter_value', return_value="1"), \
         patch.object(PostgreSQLAsyncReplication, '_is_following_promoted_cluster', return_value=True):
        
        # Configure some units to have mismatched counter values
        mock_peers_data[mock_charm.unit].get.return_value = "1"
        mock_peers_data[mock_unit1].get.return_value = "1"
        mock_peers_data[mock_unit2].get.return_value = "0"  # Different value
        
        relation = PostgreSQLAsyncReplication(mock_charm)
        relation._handle_database_start(mock_event)
        
        # Verify waiting status and deferral
        assert isinstance(mock_charm.unit.status, WaitingStatus)
        mock_event.defer.assert_called_once()

    # 3. Test when database is not started (non-leader case)
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._patroni.member_started = False
    mock_charm.unit.is_leader.return_value = False
    
    with patch.object(PostgreSQLAsyncReplication, '_get_highest_promoted_cluster_counter_value'), \
         patch('src.relations.async_replication.contextlib.suppress') as mock_suppress:
        
        mock_suppress.return_value.__enter__.return_value = None
        mock_charm._patroni.reload_patroni_configuration.side_effect = NotReadyError()
        
        relation = PostgreSQLAsyncReplication(mock_charm)
        relation._handle_database_start(mock_event)
        
        # Verify retry and deferral
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
    """Tests all conditions in _on_async_relation_changed"""

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


    with patch.object(relation, "_get_primary_cluster", return_value=None), \
         patch.object(relation, "_set_app_status") as mock_status:
        relation._on_async_relation_changed(mock_event)
        mock_status.assert_called_once()
        mock_event.defer.assert_not_called() 


    with patch.object(relation, "_get_primary_cluster", return_value="clusterX"), \
         patch.object(relation, "_configure_primary_cluster", return_value=True):
        relation._on_async_relation_changed(mock_event)
        mock_event.defer.assert_not_called()


    mock_charm.unit.is_leader.return_value = False
    with patch.object(relation, "_get_primary_cluster", return_value="clusterX"), \
         patch.object(relation, "_configure_primary_cluster", return_value=False), \
         patch.object(relation, "_is_following_promoted_cluster", return_value=True):
        relation._on_async_relation_changed(mock_event)
        mock_event.defer.assert_not_called()


    mock_charm.unit.is_leader.return_value = True
    mock_charm.is_unit_stopped = False  
    with patch.object(relation, "_get_primary_cluster", return_value="clusterX"), \
         patch.object(relation, "_configure_primary_cluster", return_value=False), \
         patch.object(relation, "_is_following_promoted_cluster", return_value=False), \
         patch.object(relation, "_stop_database", return_value=True), \
         patch.object(relation, "_get_highest_promoted_cluster_counter_value", return_value="5"):
        relation._on_async_relation_changed(mock_event)
        assert isinstance(mock_charm.unit.status, WaitingStatus)
        mock_event.defer.assert_called()


    mock_charm.is_unit_stopped = True
    with patch.object(relation, "_get_primary_cluster", return_value="clusterX"), \
         patch.object(relation, "_configure_primary_cluster", return_value=False), \
         patch.object(relation, "_is_following_promoted_cluster", return_value=False), \
         patch.object(relation, "_stop_database", return_value=True), \
         patch.object(relation, "_get_highest_promoted_cluster_counter_value", return_value="5"), \
         patch.object(relation, "_wait_for_standby_leader", return_value=True):
        relation._on_async_relation_changed(mock_event)

        mock_charm._patroni.start_patroni.assert_not_called()

    with patch.object(relation, "_get_primary_cluster", return_value="clusterX"), \
         patch.object(relation, "_configure_primary_cluster", return_value=False), \
         patch.object(relation, "_is_following_promoted_cluster", return_value=False), \
         patch.object(relation, "_stop_database", return_value=True), \
         patch.object(relation, "_get_highest_promoted_cluster_counter_value", return_value="5"), \
         patch.object(relation, "_wait_for_standby_leader", return_value=False), \
         patch.object(mock_charm._patroni, "start_patroni", return_value=True), \
         patch.object(relation, "_handle_database_start") as mock_handle_start:
        relation._on_async_relation_changed(mock_event)
        mock_charm.update_config.assert_called_once()
        mock_handle_start.assert_called_once_with(mock_event)

def test_on_secret_changed():
    """Test _on_secret_changed"""
    # 1. relation is None
    mock_charm = MagicMock()
    mock_event = MagicMock()
    
    relation = PostgreSQLAsyncReplication(mock_charm)
    
    with patch.object(
        PostgreSQLAsyncReplication,
        '_relation',
        new_callable=PropertyMock,
        return_value=None
    ):
        with patch('logging.Logger.debug') as mock_debug:
            relation._on_secret_changed(mock_event)
            
            mock_debug.assert_called_once_with("Early exit on_secret_changed: No relation found.")
            mock_event.defer.assert_not_called()

def test_stop_database():
    """Test _stop_database"""
    # Setup mock objects
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.is_unit_stopped = False
    mock_charm.unit.is_leader.return_value = False
    mock_charm._patroni.stop_patroni.return_value = True
    
    # Properly setup peers data structure
    mock_unit = MagicMock()
    mock_app = MagicMock()
    mock_charm.unit = mock_unit
    mock_charm.app = mock_app
    mock_charm._peers.data = {
        mock_app: {},
        mock_unit: {}
    }

    relation = PostgreSQLAsyncReplication(mock_charm)

    # 1. Test early exit when following promoted cluster
    with patch.object(
        PostgreSQLAsyncReplication,
        '_is_following_promoted_cluster',
        return_value=True
    ), patch('os.path.exists', return_value=True):
        result = relation._stop_database(mock_event)
        assert result is True
        mock_charm._patroni.stop_patroni.assert_not_called()

    # 3. Test deferral when patroni fails to stop
    # with patch.object(
    #     PostgreSQLAsyncReplication,
    #     '_is_following_promoted_cluster',
    #     return_value=False
    # ), patch('os.path.exists', return_value=True):
    #     mock_charm._patroni.stop_patroni.return_value = False
    #     result = relation._stop_database(mock_event)
    #     assert result is False
    #     mock_event.defer.assert_called_once()

    # 4. Test non-leader with no data path
    mock_charm._patroni.stop_patroni.return_value = True
    with patch.object(
        PostgreSQLAsyncReplication,
        '_is_following_promoted_cluster',
        return_value=False
    ), patch('os.path.exists', return_value=False):
        mock_charm.unit.is_leader.return_value = False
        result = relation._stop_database(mock_event)
        assert result is False
        mock_charm._patroni.stop_patroni.assert_not_called()

    # 5. Test leader unit behavior
    with patch.object(
        PostgreSQLAsyncReplication,
        '_is_following_promoted_cluster',
        return_value=False
    ), patch('os.path.exists', return_value=True), \
       patch.object(PostgreSQLAsyncReplication, '_configure_standby_cluster', return_value=True), \
       patch.object(PostgreSQLAsyncReplication, '_reinitialise_pgdata'), \
       patch('shutil.rmtree'), \
       patch('pathlib.Path') as mock_path:
        
        # Setup mock for the raft directory check
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


# def test_stop_database_non_leader_no_data_path():
#     """Test _stop_database when non-leader unit has no data path"""
#     # Setup mock objects
#     mock_charm = MagicMock()
#     mock_event = MagicMock()
    
#     # Set up conditions
#     mock_charm.is_unit_stopped = False
#     mock_charm.unit.is_leader.return_value = False
    
#     # Properly mock _patroni
#     mock_charm._patroni = MagicMock()
#     mock_charm._patroni.stop_patroni.return_value = (True, "Success")
    
#     # Mock system-level operations
#     with patch('subprocess.Popen') as mock_popen, \
#          patch('subprocess.run') as mock_run:
        
#         mock_popen.return_value.communicate.return_value = (b"output", b"")
#         mock_run.return_value = MagicMock(stdout=b"output", stderr=b"", returncode=0)
        
#         # Setup COMPLETE peers data structure with counters
#         mock_unit = MagicMock()
#         mock_app = MagicMock()
#         mock_charm.unit = mock_unit
#         mock_charm.app = mock_app
#         mock_charm._peers = MagicMock()
#         mock_charm._peers.data = {
#             mock_app: {
#                 'async_replication': {
#                     'status': 'active',
#                     'promoted_cluster_counter': '42'  # Must be string or int based on real code
#                 }
#             },
#             mock_unit: {
#                 'unit_id': 'postgresql/0',
#                 'relation_promoted_cluster_counter': '45'  # Must match real data format
#             }
#         }

#         relation = PostgreSQLAsyncReplication(mock_charm)
#         result = relation._stop_database(mock_event)
        
#         assert result is False
#         mock_charm._patroni.stop_patroni.assert_not_called()
#         mock_event.defer.assert_not_called()
