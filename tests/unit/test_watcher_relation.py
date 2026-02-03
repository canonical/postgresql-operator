# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the PostgreSQL watcher relation handler."""

from unittest.mock import MagicMock, PropertyMock, patch

from src.constants import RAFT_PORT
from src.relations.watcher import PostgreSQLWatcherRelation


def create_mock_charm():
    """Create a mock charm for testing."""
    mock_charm = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.cluster_name = "postgresql"
    mock_charm._patroni.unit_ip = "10.0.0.1"
    mock_charm._patroni.peers_ips = {"10.0.0.2"}
    mock_charm._patroni.raft_password = "test-raft-password"
    mock_charm.is_cluster_initialised = True
    mock_charm.update_config = MagicMock()
    return mock_charm


def create_mock_relation():
    """Create a mock relation for testing."""
    mock_relation = MagicMock()
    mock_relation.data = {
        MagicMock(): {},  # app data
        MagicMock(): {},  # unit data
    }
    mock_relation.units = set()
    return mock_relation


class TestWatcherRelation:
    """Tests for PostgreSQLWatcherRelation class."""

    def test_watcher_address_no_relation(self):
        """Test watcher_address returns None when no relation exists."""
        mock_charm = create_mock_charm()

        with patch.object(
            PostgreSQLWatcherRelation,
            "_relation",
            new_callable=PropertyMock,
            return_value=None,
        ):
            relation = PostgreSQLWatcherRelation(mock_charm)
            assert relation.watcher_address is None

    def test_watcher_address_with_relation(self):
        """Test watcher_address returns the watcher IP when available."""
        mock_charm = create_mock_charm()
        mock_relation = MagicMock()

        # Create a mock unit with unit-address
        mock_unit = MagicMock()
        mock_relation.units = {mock_unit}
        mock_relation.data = {mock_unit: {"unit-address": "10.0.0.10"}}

        with patch.object(
            PostgreSQLWatcherRelation,
            "_relation",
            new_callable=PropertyMock,
            return_value=mock_relation,
        ):
            relation = PostgreSQLWatcherRelation(mock_charm)
            assert relation.watcher_address == "10.0.0.10"

    def test_is_watcher_connected_false(self):
        """Test is_watcher_connected returns False when no watcher."""
        mock_charm = create_mock_charm()

        with patch.object(
            PostgreSQLWatcherRelation,
            "watcher_address",
            new_callable=PropertyMock,
            return_value=None,
        ):
            relation = PostgreSQLWatcherRelation(mock_charm)
            assert relation.is_watcher_connected is False

    def test_is_watcher_connected_true(self):
        """Test is_watcher_connected returns True when watcher exists."""
        mock_charm = create_mock_charm()

        with patch.object(
            PostgreSQLWatcherRelation,
            "watcher_address",
            new_callable=PropertyMock,
            return_value="10.0.0.10",
        ):
            relation = PostgreSQLWatcherRelation(mock_charm)
            assert relation.is_watcher_connected is True

    def test_get_watcher_raft_address(self):
        """Test get_watcher_raft_address returns formatted address."""
        mock_charm = create_mock_charm()

        with patch.object(
            PostgreSQLWatcherRelation,
            "watcher_address",
            new_callable=PropertyMock,
            return_value="10.0.0.10",
        ):
            relation = PostgreSQLWatcherRelation(mock_charm)
            assert relation.get_watcher_raft_address() == f"10.0.0.10:{RAFT_PORT}"

    def test_get_watcher_raft_address_no_watcher(self):
        """Test get_watcher_raft_address returns None when no watcher."""
        mock_charm = create_mock_charm()

        with patch.object(
            PostgreSQLWatcherRelation,
            "watcher_address",
            new_callable=PropertyMock,
            return_value=None,
        ):
            relation = PostgreSQLWatcherRelation(mock_charm)
            assert relation.get_watcher_raft_address() is None

    def test_on_watcher_relation_joined_not_leader(self):
        """Test relation joined event is ignored for non-leader units."""
        mock_charm = create_mock_charm()
        mock_charm.unit.is_leader.return_value = False
        mock_event = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(relation, "_get_or_create_watcher_secret") as mock_secret:
            relation._on_watcher_relation_joined(mock_event)
            mock_secret.assert_not_called()

    def test_on_watcher_relation_joined_leader(self):
        """Test relation joined event creates secret for leader."""
        mock_charm = create_mock_charm()
        mock_event = MagicMock()
        mock_secret = MagicMock()
        mock_secret.id = "secret:abc123"

        relation = PostgreSQLWatcherRelation(mock_charm)

        with (
            patch.object(relation, "_get_or_create_watcher_secret", return_value=mock_secret),
            patch.object(relation, "_update_relation_data") as mock_update,
        ):
            relation._on_watcher_relation_joined(mock_event)
            mock_secret.grant.assert_called_once_with(mock_event.relation)
            mock_update.assert_called_once_with(mock_event.relation)

    def test_on_watcher_relation_joined_no_secret(self):
        """Test relation joined event defers when secret creation fails."""
        mock_charm = create_mock_charm()
        mock_event = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(relation, "_get_or_create_watcher_secret", return_value=None):
            relation._on_watcher_relation_joined(mock_event)
            mock_event.defer.assert_called_once()

    def test_on_watcher_relation_changed_not_initialized(self):
        """Test relation changed event defers when cluster not initialized."""
        mock_charm = create_mock_charm()
        mock_charm.is_cluster_initialised = False
        mock_event = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)
        relation._on_watcher_relation_changed(mock_event)

        mock_event.defer.assert_called_once()

    def test_on_watcher_relation_changed_updates_config(self):
        """Test relation changed event updates Patroni config."""
        mock_charm = create_mock_charm()
        mock_event = MagicMock()

        # Setup mock relation with watcher unit
        mock_unit = MagicMock()
        mock_event.relation.units = {mock_unit}
        mock_event.relation.data = {mock_unit: {"unit-address": "10.0.0.10"}}

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(relation, "_update_relation_data"):
            relation._on_watcher_relation_changed(mock_event)
            mock_charm.update_config.assert_called_once()

    def test_on_watcher_relation_broken_updates_config(self):
        """Test relation broken event updates Patroni config."""
        mock_charm = create_mock_charm()
        mock_event = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)
        relation._on_watcher_relation_broken(mock_event)

        mock_charm.update_config.assert_called_once()

    def test_on_watcher_relation_broken_not_initialized(self):
        """Test relation broken is ignored when cluster not initialized."""
        mock_charm = create_mock_charm()
        mock_charm.is_cluster_initialised = False
        mock_event = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)
        relation._on_watcher_relation_broken(mock_event)

        mock_charm.update_config.assert_not_called()

    def test_update_relation_data_not_leader(self):
        """Test _update_relation_data does nothing for non-leader."""
        mock_charm = create_mock_charm()
        mock_charm.unit.is_leader.return_value = False
        mock_relation = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)
        relation._update_relation_data(mock_relation)

        # Should not try to update relation data
        assert not mock_relation.data[mock_charm.app].update.called

    def test_update_relation_data_leader(self):
        """Test _update_relation_data populates relation data correctly."""
        mock_charm = create_mock_charm()
        mock_charm._units_ips = ["10.0.0.1", "10.0.0.2"]  # Mock PostgreSQL endpoints
        mock_charm._unit_ip = "10.0.0.1"
        mock_relation = MagicMock()
        mock_relation.data = {
            mock_charm.app: {},
            mock_charm.unit: {},
        }

        mock_secret = MagicMock()
        mock_secret.id = "secret:abc123"

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(mock_charm.model, "get_secret", return_value=mock_secret):
            relation._update_relation_data(mock_relation)

        # Verify app data was updated
        app_data = mock_relation.data[mock_charm.app]
        assert "cluster-name" in app_data
        assert app_data["cluster-name"] == "postgresql"
        assert "raft-secret-id" in app_data
        assert "pg-endpoints" in app_data
        assert "raft-partner-addrs" in app_data
        assert "raft-port" in app_data

        # Verify unit data was updated
        unit_data = mock_relation.data[mock_charm.unit]
        assert "unit-address" in unit_data

    def test_update_watcher_secret_not_leader(self):
        """Test update_watcher_secret does nothing for non-leader."""
        mock_charm = create_mock_charm()
        mock_charm.unit.is_leader.return_value = False

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(mock_charm.model, "get_secret") as mock_get:
            relation.update_watcher_secret()
            mock_get.assert_not_called()

    def test_update_watcher_secret_leader(self):
        """Test update_watcher_secret updates secret content."""
        mock_charm = create_mock_charm()
        mock_secret = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(mock_charm.model, "get_secret", return_value=mock_secret):
            relation.update_watcher_secret()
            mock_secret.set_content.assert_called_once()


class TestWatcherRelationSecrets:
    """Tests for secret management in watcher relation."""

    def test_get_or_create_watcher_secret_existing(self):
        """Test _get_or_create_watcher_secret returns existing secret."""
        mock_charm = create_mock_charm()
        mock_secret = MagicMock()

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(mock_charm.model, "get_secret", return_value=mock_secret):
            result = relation._get_or_create_watcher_secret()
            assert result == mock_secret

    def test_get_or_create_watcher_secret_creates_new(self):
        """Test _get_or_create_watcher_secret creates new secret."""
        mock_charm = create_mock_charm()
        mock_secret = MagicMock()

        from ops import SecretNotFoundError

        relation = PostgreSQLWatcherRelation(mock_charm)

        with (
            patch.object(
                mock_charm.model,
                "get_secret",
                side_effect=SecretNotFoundError("not found"),
            ),
            patch.object(
                mock_charm.model.app,
                "add_secret",
                return_value=mock_secret,
            ),
        ):
            result = relation._get_or_create_watcher_secret()
            assert result == mock_secret
            mock_charm.model.app.add_secret.assert_called_once()

    def test_get_or_create_watcher_secret_no_raft_password(self):
        """Test _get_or_create_watcher_secret returns None without password."""
        mock_charm = create_mock_charm()
        mock_charm._patroni.raft_password = None

        from ops import SecretNotFoundError

        relation = PostgreSQLWatcherRelation(mock_charm)

        with patch.object(
            mock_charm.model,
            "get_secret",
            side_effect=SecretNotFoundError("not found"),
        ):
            result = relation._get_or_create_watcher_secret()
            assert result is None
