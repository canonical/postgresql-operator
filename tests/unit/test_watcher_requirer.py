# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the watcher requirer relation handler (AZ co-location logic)."""

import json
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
    mock_relation.app = MagicMock()
    mock_relation.app.name = "postgresql"
    mock_data[mock_relation.app] = {}
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

            # Mock framework.model to make self.model work
            mock_framework = MagicMock()
            mock_framework.model = mock_charm.model
            handler.framework = mock_framework

            # Mock model.relations
            mock_charm.model.relations.get.return_value = [mock_relation]

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

        with (
            patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False),
            patch(
                "relations.watcher_requirer.RaftController.get_status",
                return_value={"connected": True},
            ),
        ):
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

        with (
            patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az1"}, clear=False),
            patch(
                "relations.watcher_requirer.RaftController.get_status",
                return_value={"connected": True},
            ),
        ):
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

        with (
            patch.dict("os.environ", {"JUJU_AVAILABILITY_ZONE": "az3"}, clear=False),
            patch(
                "relations.watcher_requirer.RaftController.get_status",
                return_value={"connected": True},
            ),
        ):
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
        with (
            patch.dict("os.environ", env, clear=True),
            patch(
                "relations.watcher_requirer.RaftController.get_status",
                return_value={"connected": True},
            ),
        ):
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


class TestWatcherRelationLifecycle:
    """Tests for watcher relation lifecycle cleanup."""

    def test_relation_broken_removes_port(self):
        """Relation-broken removes the Raft service and releases the allocated port."""
        mock_charm = create_mock_charm()
        mock_relation = MagicMock()
        mock_relation.id = 42
        mock_event = MagicMock()
        mock_event.relation = mock_relation

        with (
            patch.object(WatcherRequirerHandler, "__init__", return_value=None),
            patch("relations.watcher_requirer.RaftController.remove_service") as _remove_service,
        ):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm
            handler._release_port_for_relation = MagicMock()

            mock_framework = MagicMock()
            mock_framework.model = mock_charm.model
            handler.framework = mock_framework

            mock_charm.model.relations.get.return_value = []

            handler._on_watcher_relation_broken(mock_event)

            _remove_service.assert_called_once_with()
            handler._release_port_for_relation.assert_called_once_with(42)


class TestWatcherActions:
    """Tests for watcher actions output formatting."""

    def _build_handler(self):
        mock_charm = create_mock_charm()
        mock_framework = MagicMock()
        mock_framework.model = mock_charm.model
        with patch.object(WatcherRequirerHandler, "__init__", return_value=None):
            handler = WatcherRequirerHandler.__new__(WatcherRequirerHandler)
            handler.charm = mock_charm
            handler.framework = mock_framework
            handler._get_standby_clusters = MagicMock(return_value=[])
            return handler, mock_charm

    def test_get_cluster_status_serializes_json_result(self):
        """Action output is a JSON string in the `status` key."""
        handler, mock_charm = self._build_handler()
        relation = MagicMock()
        relation.id = 1
        mock_charm.model.relations.get.return_value = [relation]
        handler._get_cluster_name = MagicMock(return_value="cluster-a")
        handler._format_cluster_status = MagicMock(return_value={"raft": {"has_quorum": True}})

        event = MagicMock()
        event.params = {"standby-clusters": False}

        handler._on_get_cluster_status(event)

        event.set_results.assert_called_once()
        results = event.set_results.call_args.args[0]
        assert results["success"] == "True"
        parsed = json.loads(results["status"])
        assert parsed["raft"]["has_quorum"] is True

    def test_get_cluster_status_no_relations_returns_empty_json(self):
        """No-related-cluster response returns an empty JSON object string."""
        handler, mock_charm = self._build_handler()
        mock_charm.model.relations.get.return_value = []

        event = MagicMock()
        event.params = {}

        handler._on_get_cluster_status(event)

        event.set_results.assert_called_once_with({"success": "True", "status": "{}"})

    def test_get_cluster_status_cluster_filter_not_found_fails(self):
        """Unknown cluster filter fails instead of returning status."""
        handler, mock_charm = self._build_handler()
        relation = MagicMock()
        relation.id = 1
        mock_charm.model.relations.get.return_value = [relation]
        handler._get_cluster_name = MagicMock(return_value="cluster-a")

        event = MagicMock()
        event.params = {"cluster-name": "cluster-missing"}

        handler._on_get_cluster_status(event)

        event.fail.assert_called_once()
        event.set_results.assert_not_called()

    def test_get_cluster_status_cluster_set_uses_role_and_links(self):
        """Cluster-set output honors role and includes linked standby clusters."""
        handler, mock_charm = self._build_handler()
        rel_primary = MagicMock()
        rel_primary.id = 1
        rel_standby = MagicMock()
        rel_standby.id = 2
        mock_charm.model.relations.get.return_value = [rel_primary, rel_standby]
        handler._get_cluster_name = MagicMock(side_effect=["cluster-a", "cluster-b"])
        handler._format_cluster_status = MagicMock(
            side_effect=[
                {
                    "clusterrole": "primary",
                    "status": "ok",
                    "primary": "10.0.0.1:5432",
                    "timeline": 1,
                },
                {
                    "clusterrole": "standby",
                    "status": "ok",
                    "primary": None,
                    "timeline": 1,
                },
            ]
        )
        handler._get_standby_clusters = MagicMock(side_effect=[["cluster-b"], ["cluster-a"]])

        event = MagicMock()
        event.params = {"standby-clusters": True}

        handler._on_get_cluster_status(event)

        results = event.set_results.call_args.args[0]
        payload = json.loads(results["status"])
        assert payload["primary_cluster"] == "cluster-a"
        assert payload["clusters"]["cluster-a"]["linked_standby_clusters"] == ["cluster-b"]
        assert payload["clusters"]["cluster-b"]["replication_status"] == "streaming"

    def test_trigger_health_check_marks_non_dict_result_unhealthy(self):
        """Non-dict health results are treated as unhealthy values."""
        handler, mock_charm = self._build_handler()
        relation = MagicMock()
        relation.id = 1
        mock_charm.model.relations.get.return_value = [relation]
        handler._get_pg_endpoints = MagicMock(return_value=["10.0.0.1"])
        handler._build_ip_maps = MagicMock(return_value=({}, {"10.0.0.1": "postgresql/0"}))
        handler._get_cluster_name = MagicMock(return_value="cluster-a")

        event = MagicMock()

        with patch("watcher_health.HealthChecker") as mock_health_checker:
            mock_health_checker.return_value.check_all_endpoints.return_value = {
                "10.0.0.1": ["unexpected"]
            }
            handler._on_trigger_health_check(event)

        results = event.set_results.call_args.args[0]
        payload = json.loads(results["health-check"])
        assert payload["healthy-count"] == 0
        assert payload["total-count"] == 1
        assert payload["clusters"][0]["endpoints"]["postgresql/0"] == "unhealthy"

    def test_format_cluster_status_marks_standby_when_recovery_only(self):
        """Cluster role becomes standby when healthy members are in recovery."""
        handler, _ = self._build_handler()
        relation = MagicMock()
        relation.id = 7

        handler._get_cluster_name = MagicMock(return_value="cluster-a")
        handler._get_pg_endpoints = MagicMock(return_value=["10.0.0.1"])
        handler._build_ip_maps = MagicMock(return_value=({}, {"10.0.0.1": "postgresql/0"}))
        handler._get_port_for_relation = MagicMock(return_value=2222)
        handler._get_pg_version = MagicMock(return_value="16")

        raft_controller = MagicMock()
        raft_controller.get_status.return_value = {
            "running": True,
            "connected": True,
            "has_quorum": True,
            "leader": "10.0.0.1:2222",
            "members": ["10.0.0.1:2222"],
        }
        handler._get_or_create_raft_controller = MagicMock(return_value=raft_controller)

        with patch("watcher_health.HealthChecker") as mock_health_checker:
            mock_health_checker.return_value.check_all_endpoints.return_value = {
                "10.0.0.1": {"healthy": True, "is_in_recovery": True}
            }
            status = handler._format_cluster_status(relation)

        assert status["clusterrole"] == "standby"
        assert status["primary"] is None

    def test_format_cluster_status_uses_unit_address_when_binding_missing(self):
        """Watcher topology address falls back to relation unit-address."""
        handler, mock_charm = self._build_handler()
        relation = MagicMock()
        relation.id = 7
        relation.app = MagicMock()
        relation.data = {mock_charm.unit: {"unit-address": "10.1.1.7"}, relation.app: {}}
        mock_charm.model.get_binding.return_value = None

        handler._get_cluster_name = MagicMock(return_value="cluster-a")
        handler._get_pg_endpoints = MagicMock(return_value=[])
        handler._build_ip_maps = MagicMock(return_value=({}, {}))
        handler._get_port_for_relation = MagicMock(return_value=2222)

        raft_controller = MagicMock()
        raft_controller.get_status.return_value = {
            "running": True,
            "connected": True,
            "has_quorum": True,
            "leader": None,
            "members": [],
        }
        handler._get_or_create_raft_controller = MagicMock(return_value=raft_controller)

        status = handler._format_cluster_status(relation)
        assert status["topology"]["pg-watcher/0"]["address"] == "10.1.1.7:2222"

    def test_format_cluster_status_does_not_emit_none_port_address(self):
        """Watcher topology address is None when no IP source is available."""
        handler, mock_charm = self._build_handler()
        relation = MagicMock()
        relation.id = 7
        relation.app = MagicMock()
        relation.data = {mock_charm.unit: {}, relation.app: {}}
        mock_charm.model.get_binding.return_value = None

        handler._get_cluster_name = MagicMock(return_value="cluster-a")
        handler._get_pg_endpoints = MagicMock(return_value=[])
        handler._build_ip_maps = MagicMock(return_value=({}, {}))
        handler._get_port_for_relation = MagicMock(return_value=2222)

        raft_controller = MagicMock()
        raft_controller.get_status.return_value = {
            "running": True,
            "connected": True,
            "has_quorum": True,
            "leader": None,
            "members": [],
        }
        handler._get_or_create_raft_controller = MagicMock(return_value=raft_controller)

        status = handler._format_cluster_status(relation)
        assert status["topology"]["pg-watcher/0"]["address"] is None
