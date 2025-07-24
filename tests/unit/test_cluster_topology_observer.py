# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import signal
import sys
from unittest.mock import Mock, PropertyMock, mock_open, patch, sentinel

import pytest
from ops.charm import CharmBase
from ops.model import ActiveStatus, Relation, WaitingStatus
from ops.testing import Harness

from cluster import Patroni
from cluster_topology_observer import (
    ClusterTopologyChangeCharmEvents,
    ClusterTopologyObserver,
)
from scripts.cluster_topology_observer import (
    UnreachableUnitsError,
    check_for_database_changes,
    dispatch,
    main,
)


class MockCharm(CharmBase):
    on = ClusterTopologyChangeCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        self.observer = ClusterTopologyObserver(self, "test-command")
        self.framework.observe(self.on.cluster_topology_change, self._on_cluster_topology_change)

    def _on_cluster_topology_change(self, _) -> None:
        self.unit.status = ActiveStatus("cluster topology changed")

    @property
    def _patroni(self) -> Patroni:
        return Mock(_patroni_url="http://1.1.1.1:8008/", peers_ips={}, verify=True)

    @property
    def _peers(self) -> Relation | None:
        return None


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(MockCharm, meta="name: test-charm")
    harness.begin()
    yield harness
    harness.cleanup()


def test_start_observer(harness):
    with (
        patch("builtins.open") as _open,
        patch("subprocess.Popen") as _popen,
        patch.object(MockCharm, "_peers", new_callable=PropertyMock) as _peers,
    ):
        # Test that nothing is done if there is already a running process.
        _peers.return_value = Mock(data={harness.charm.unit: {"observer-pid": "1"}})
        harness.charm.observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if the charm is not in an active status.
        harness.charm.unit.status = WaitingStatus()
        _peers.return_value = Mock(data={harness.charm.unit: {}})
        harness.charm.observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if peer relation is not available yet.
        harness.charm.unit.status = ActiveStatus()
        _peers.return_value = None
        harness.charm.observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if there is already a running process.
        _peers.return_value = Mock(data={harness.charm.unit: {}})
        _popen.return_value = Mock(pid=1)
        harness.charm.observer.start_observer()
        _popen.assert_called_once()


def test_start_observer_already_running(harness):
    with (
        patch("builtins.open") as _open,
        patch("subprocess.Popen") as _popen,
        patch("os.kill") as _kill,
        patch.object(MockCharm, "_peers", new_callable=PropertyMock) as _peers,
    ):
        harness.charm.unit.status = ActiveStatus()
        _peers.return_value = Mock(data={harness.charm.unit: {"observer-pid": "1234"}})
        harness.charm.observer.start_observer()
        _kill.assert_called_once_with(1234, 0)
        assert not _popen.called
        _kill.reset_mock()

        # If process is already dead, it should restart
        _kill.side_effect = OSError
        harness.charm.observer.start_observer()
        _kill.assert_called_once_with(1234, 0)
        _popen.assert_called_once()
        _kill.reset_mock()


def test_stop_observer(harness):
    with (
        patch("os.kill") as _kill,
        patch.object(MockCharm, "_peers", new_callable=PropertyMock) as _peers,
    ):
        # Test that nothing is done if there is no process running.
        harness.charm.observer.stop_observer()
        _kill.assert_not_called()

        _peers.return_value = Mock(data={harness.charm.unit: {}})
        harness.charm.observer.stop_observer()
        _kill.assert_not_called()

        # Test that the process is killed.
        _peers.return_value = Mock(data={harness.charm.unit: {"observer-pid": "1"}})
        harness.charm.observer.stop_observer()
        _kill.assert_called_once_with(1, signal.SIGINT)
        _kill.reset_mock()

        # Dead process doesn't break the script
        _peers.return_value = Mock(data={harness.charm.unit: {"observer-pid": "1"}})
        _kill.side_effect = OSError
        harness.charm.observer.stop_observer()
        _kill.assert_called_once_with(1, signal.SIGINT)
        _kill.reset_mock()


def test_dispatch(harness):
    with patch("subprocess.run") as _run:
        command = "test-command"
        charm_dir = "/path"
        dispatch(command, harness.charm.unit.name, charm_dir, "cluster_topology_change")
        _run.assert_called_once_with([
            command,
            "-u",
            harness.charm.unit.name,
            f"JUJU_DISPATCH_PATH=hooks/cluster_topology_change {charm_dir}/dispatch",
        ])


async def test_main():
    with (
        patch("scripts.cluster_topology_observer.check_for_database_changes"),
        patch.object(
            sys,
            "argv",
            ["cmd", "http://server1:8008,http://server2:8008", "run_cmd", "unit/0", "charm_dir"],
        ),
        patch("scripts.cluster_topology_observer.sleep", return_value=None),
        patch("scripts.cluster_topology_observer.AsyncClient") as _async_client,
        patch("scripts.cluster_topology_observer.subprocess") as _subprocess,
        patch("scripts.cluster_topology_observer.create_default_context") as _context,
    ):
        mock1 = Mock()
        mock1.json.return_value = {
            "members": [
                {"name": "unit-2", "api_url": "http://server3:8008/patroni", "role": "standby"},
                {"name": "unit-0", "api_url": "http://server1:8008/patroni", "role": "leader"},
            ]
        }
        mock2 = Mock()
        mock2.json.return_value = {
            "members": [
                {"name": "unit-2", "api_url": "https://server3:8008/patroni", "role": "leader"},
            ]
        }
        async with _async_client() as cli:
            _get = cli.get
            _get.side_effect = [
                mock1,
                Exception,
                mock2,
            ]
        with pytest.raises(UnreachableUnitsError):
            await main()
        _async_client.assert_any_call(timeout=5, verify=_context.return_value)
        _get.assert_any_call("http://server1:8008/cluster")
        _get.assert_any_call("http://server3:8008/cluster")

        _subprocess.run.assert_called_once_with([
            "run_cmd",
            "-u",
            "unit/0",
            "JUJU_DISPATCH_PATH=hooks/cluster_topology_change charm_dir/dispatch",
        ])


def test_check_for_database_changes():
    with (
        patch("scripts.cluster_topology_observer.subprocess") as _subprocess,
        patch("scripts.cluster_topology_observer.psycopg2") as _psycopg2,
    ):
        run_cmd = "run_cmd"
        unit = "unit/0"
        charm_dir = "charm_dir"
        mock = mock_open(
            read_data="""postgresql:
  authentication:
    superuser:
      username: test_user
      password: test_password"""
        )
        with patch("builtins.open", mock, create=True):
            _cursor = _psycopg2.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            _cursor.fetchall.return_value = sentinel.databases

            # Test the first time this function is called.
            result = check_for_database_changes(run_cmd, unit, charm_dir, None)
            assert result == sentinel.databases
            _subprocess.run.assert_not_called()
            _psycopg2.connect.assert_called_once_with(
                "dbname='postgres' user='operator' host='localhost'password='test_password' connect_timeout=1"
            )
            _cursor.execute.assert_called_once_with("SELECT datname,datacl FROM pg_database;")

            # Test when the databases changed.
            _cursor.fetchall.return_value = sentinel.databases_changed
            result = check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == sentinel.databases_changed

            _subprocess.run.assert_called_once_with([
                run_cmd,
                "-u",
                unit,
                f"JUJU_DISPATCH_PATH=hooks/databases_change {charm_dir}/dispatch",
            ])

            # Test when the databases haven't changed.
            _subprocess.reset_mock()
            check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == sentinel.databases_changed
            _subprocess.run.assert_not_called()
