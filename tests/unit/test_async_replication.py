from unittest.mock import MagicMock, PropertyMock, patch
from src.relations.async_replication import PostgreSQLAsyncReplication
from src.relations.async_replication import READ_ONLY_MODE_BLOCKING_MESSAGE
from src.relations.async_replication import StandbyClusterAlreadyPromotedError

def test_can_promote_cluster():
    """Tests all conditions in _can_promote_cluster"""
    
    # 1. Test when cluster is not initialized
    mock_charm = MagicMock()
    mock_event = MagicMock()
    type(mock_charm).is_cluster_initialised = PropertyMock(return_value=False)
    
    with patch.object(PostgreSQLAsyncReplication, '_get_primary_cluster') as mock_get_primary:
        relation = PostgreSQLAsyncReplication(mock_charm)
        mock_get_primary.return_value = (MagicMock(), "0")
        
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
