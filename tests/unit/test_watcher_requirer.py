# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the watcher requirer relation handler (AZ co-location logic)."""

from unittest.mock import MagicMock, patch

from ops import ActiveStatus, BlockedStatus, WaitingStatus

from src.relations.watcher_requirer import WatcherRequirerHandler


def create_mock_charm(profile="testing"):
    """Create a mock charm for watcher requirer testing."""
    mock_charm = MagicMock()
    mock_charm.config = MagicMock()
    mock_charm.config.profile = profile
    mock_charm.unit.name = "pg-watcher/0"
    return mock_charm


def create_mock_relation(units_with_az=None):
    """Create a mock relation with units that have AZ data.

    Args:
        units_with_az: Dict mapping unit names to their AZ values.
            Example: {"postgresql/0": "az1", "postgresql/1": "az2"}
    """
    mock_relation = MagicMock()
    mock_relation.id = 42

    if units_with_az is None:
        units_with_az = {}

    mock_units = []
    mock_data = {}
    for unit_name, az in units_with_az.items():
        mock_unit = MagicMock()
        mock_unit.name = unit_name
        mock_units.append(mock_unit)
        unit_data = {}
        if az is not None:
            unit_data["unit-az"] = az
        mock_data[mock_unit] = unit_data

    mock_relation.units = set(mock_units)
    mock_relation.data = mock_data
    return mock_relation


class TestAZColocation:
    """Tests for AZ co-location detection and enforcement."""

    def test_check_az_colocation_no_az_set(self):
        """No warning when JUJU_AVAILABILITY_ZONE is not set."""
        mock_charm = create_mock_charm()
        relation = create_mock_relation({"postgresql/0": "az1"})

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm

            with patch.dict("os.environ", {}, clear=True):
                result = handler._check_az_colocation(relation)
                assert result is None

    def test_check_az_colocation_different_az(self):
        """No warning when watcher is in a different AZ."""
        mock_charm = create_mock_charm()
        relation = create_mock_relation({"postgresql/0": "az1", "postgresql/1": "az2"})

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm

            with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az3"}, clear=False):
                result = handler._check_az_colocation(relation)
                assert result is None

    def test_check_az_colocation_same_az(self):
        """Warning returned when watcher shares AZ with a PostgreSQL unit."""
        mock_charm = create_mock_charm()
        relation = create_mock_relation({"postgresql/0": "az1", "postgresql/1": "az2"})

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm

            with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False):
                result = handler._check_az_colocation(relation)
                assert result is not None
                assert "az1" in result
                assert "postgresql/0" in result

    def test_check_az_colocation_multiple_colocated(self):
        """Warning lists all co-located units."""
        mock_charm = create_mock_charm()
        relation = create_mock_relation({"postgresql/0": "az1", "postgresql/1": "az1"})

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm

            with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False):
                result = handler._check_az_colocation(relation)
                assert result is not None
                assert "postgresql/0" in result
                assert "postgresql/1" in result

    def test_check_az_colocation_pg_unit_no_az(self):
        """No warning when PostgreSQL unit has no AZ set."""
        mock_charm = create_mock_charm()
        relation = create_mock_relation({"postgresql/0": None})

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm

            with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False):
                result = handler._check_az_colocation(relation)
                assert result is None


class TestAZProfileEnforcement:
    """Tests for profile-based AZ enforcement (testing=warning, production=blocked)."""

    def _setup_handler_with_relations(self, profile, watcher_az, pg_units_az):
        """Create a handler with mocked relations for update_status testing.

        Args:
            profile: "testing" or "production"
            watcher_az: The watcher's AZ or None
            pg_units_az: Dict of unit_name -> az for PostgreSQL units
        """
        mock_charm = create_mock_charm(profile=profile)
        mock_relation = create_mock_relation(pg_units_az)

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm
            handler._raft_controllers = {}

            # Mock framework.model to make self.model work
            mock_framework = MagicMock()
            mock_framework.model = mock_charm.model
            handler.framework = mock_framework

            # Mock model.relations
            mock_charm.model.relations.get.return_value = [mock_relation]

            # Mock raft controller
            mock_raft = MagicMock()
            mock_raft.get_status.return_value = {"connected": True}
            handler._raft_controllers[mock_relation.id] = mock_raft

            # Mock _get_pg_endpoints
            handler._get_pg_endpoints = MagicMock(return_value=list(pg_units_az.keys()))
            handler._update_unit_address_if_changed = MagicMock()

            return handler, mock_charm, watcher_az

    def test_testing_profile_same_az_sets_active_with_warning(self):
        """With profile=testing and same AZ, status is Active with WARNING."""
        handler, mock_charm, _ = self._setup_handler_with_relations(
            profile="testing",
            watcher_az="az1",
            pg_units_az={"postgresql/0": "az1", "postgresql/1": "az2"},
        )

        with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False):
            handler._on_update_status(MagicMock())

        status = mock_charm.unit.status
        assert isinstance(status, ActiveStatus), (
            f"Expected ActiveStatus, got {type(status)}: {status}"
        )
        assert "WARNING" in status.message

    def test_production_profile_same_az_sets_blocked(self):
        """With profile=production and same AZ, status is Blocked."""
        handler, mock_charm, _ = self._setup_handler_with_relations(
            profile="production",
            watcher_az="az1",
            pg_units_az={"postgresql/0": "az1", "postgresql/1": "az2"},
        )

        with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False):
            handler._on_update_status(MagicMock())

        status = mock_charm.unit.status
        assert isinstance(status, BlockedStatus), (
            f"Expected BlockedStatus, got {type(status)}: {status}"
        )
        assert "AZ co-location" in status.message

    def test_production_profile_different_az_sets_active(self):
        """With profile=production and different AZ, status is Active (no block)."""
        handler, mock_charm, _ = self._setup_handler_with_relations(
            profile="production",
            watcher_az="az3",
            pg_units_az={"postgresql/0": "az1", "postgresql/1": "az2"},
        )

        with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az3"}, clear=False):
            handler._on_update_status(MagicMock())

        status = mock_charm.unit.status
        assert isinstance(status, ActiveStatus), (
            f"Expected ActiveStatus, got {type(status)}: {status}"
        )
        assert "WARNING" not in status.message

    def test_no_az_no_block(self):
        """When JUJU_AVAILABILITY_ZONE is not set, no blocking regardless of profile."""
        handler, mock_charm, _ = self._setup_handler_with_relations(
            profile="production",
            watcher_az=None,
            pg_units_az={"postgresql/0": "az1", "postgresql/1": "az2"},
        )

        env = {k: v for k, v in __import__("os").environ.items() if k != "JUJU_AVAILABILITY_ZONE"}
        with patch.dict("os.environ", env, clear=True):
            handler._on_update_status(MagicMock())

        status = mock_charm.unit.status
        assert isinstance(status, ActiveStatus), (
            f"Expected ActiveStatus, got {type(status)}: {status}"
        )

    def test_no_raft_connection_sets_waiting(self):
        """When Raft is not connected, status is Waiting regardless of AZ."""
        mock_charm = create_mock_charm(profile="production")
        mock_relation = create_mock_relation({"postgresql/0": "az1"})

        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm
            handler._raft_controllers = {}
            mock_framework = MagicMock()
            mock_framework.model = mock_charm.model
            handler.framework = mock_framework
            mock_charm.model.relations.get.return_value = [mock_relation]

            mock_raft = MagicMock()
            mock_raft.get_status.return_value = {"connected": False}
            handler._raft_controllers[mock_relation.id] = mock_raft
            handler._get_pg_endpoints = MagicMock(return_value=[])
            handler._update_unit_address_if_changed = MagicMock()

            with patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False):
                handler._on_update_status(MagicMock())

            status = mock_charm.unit.status
            assert isinstance(status, WaitingStatus)
