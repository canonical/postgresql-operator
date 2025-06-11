# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import signal
import sys
from json import dumps
from unittest.mock import Mock, PropertyMock, call, mock_open, patch, sentinel

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
    check_for_authorisation_rules_changes,
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


def test_main():
    with (
        patch("scripts.cluster_topology_observer.check_for_database_changes"),
        patch("scripts.cluster_topology_observer.check_for_authorisation_rules_changes"),
        patch.object(
            sys,
            "argv",
            ["cmd", "http://server1:8008,http://server2:8008", "run_cmd", "unit/0", "charm_dir"],
        ),
        patch("scripts.cluster_topology_observer.sleep", return_value=None),
        patch("scripts.cluster_topology_observer.urlopen") as _urlopen,
        patch("scripts.cluster_topology_observer.subprocess") as _subprocess,
        patch(
            "scripts.cluster_topology_observer.create_default_context",
            return_value=sentinel.sslcontext,
        ),
    ):
        response1 = {
            "members": [
                {"name": "unit-2", "api_url": "http://server3:8008/patroni", "role": "standby"},
                {"name": "unit-0", "api_url": "http://server1:8008/patroni", "role": "leader"},
            ]
        }
        mock1 = Mock()
        mock1.read.return_value = dumps(response1)
        response2 = {
            "members": [
                {"name": "unit-2", "api_url": "https://server3:8008/patroni", "role": "leader"},
            ]
        }
        mock2 = Mock()
        mock2.read.return_value = dumps(response2)
        _urlopen.side_effect = [mock1, Exception, mock2]
        with pytest.raises(UnreachableUnitsError):
            main()
        assert _urlopen.call_args_list == [
            # Iteration 1. server2 is not called
            call("http://server1:8008/cluster", timeout=5, context=sentinel.sslcontext),
            # Iteration 2 local unit server1 is called first
            call("http://server1:8008/cluster", timeout=5, context=sentinel.sslcontext),
            call("http://server3:8008/cluster", timeout=5, context=sentinel.sslcontext),
            # Iteration 3 Last known member is server3
            call("https://server3:8008/cluster", timeout=5, context=sentinel.sslcontext),
        ]

        _subprocess.run.assert_called_once_with([
            "run_cmd",
            "-u",
            "unit/0",
            "JUJU_DISPATCH_PATH=hooks/cluster_topology_change charm_dir/dispatch",
        ])


def test_check_for_authorisation_rules_changes():
    with patch("scripts.cluster_topology_observer.subprocess") as _subprocess:
        run_cmd = "run_cmd"
        unit = "unit/0"
        charm_dir = "charm_dir"

        # Test the first time this function is called.
        mock = mock_open(
            read_data="""local               database1  user1  trust
host                database1  user2  address     scram-sha-256
hostssl             database1,database2  user3  address     scram-sha-256"""
        )
        with patch("builtins.open", mock, create=True):
            result = check_for_authorisation_rules_changes(run_cmd, unit, charm_dir, [])
            assert result == [
                "local               database1  user1  trust",
                "host                database1  user2  address     scram-sha-256",
                "hostssl             database1,database2  user3  address     scram-sha-256",
            ]
            _subprocess.run.assert_not_called()

        # Test when the authorisation rules file has been changed.
        mock = mock_open(
            read_data="""local               database1  user1  trust
host                database1  user2  address     scram-sha-256
hostssl             database1,database2  user3  address     scram-sha-256
hostssl             database3  user4  address     scram-sha-256"""
        )
        with patch("builtins.open", mock, create=True):
            result = check_for_authorisation_rules_changes(run_cmd, unit, charm_dir, result)
            assert result == [
                "local               database1  user1  trust",
                "host                database1  user2  address     scram-sha-256",
                "hostssl             database1,database2  user3  address     scram-sha-256",
                "hostssl             database3  user4  address     scram-sha-256",
            ]
            _subprocess.run.assert_called_once_with([
                run_cmd,
                "-u",
                unit,
                f"JUJU_DISPATCH_PATH=hooks/authorisation_rules_change {charm_dir}/dispatch",
            ])

            # Test when the authorisation rules file hasn't been changed.
            _subprocess.reset_mock()
            result = check_for_authorisation_rules_changes(run_cmd, unit, charm_dir, result)
            assert result == [
                "local               database1  user1  trust",
                "host                database1  user2  address     scram-sha-256",
                "hostssl             database1,database2  user3  address     scram-sha-256",
                "hostssl             database3  user4  address     scram-sha-256",
            ]
            _subprocess.run.assert_not_called()


def test_check_for_database_changes():
    with patch("scripts.cluster_topology_observer.subprocess") as _subprocess:
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
            # Test the first time this function is called.
            _subprocess.check_output.return_value = b"datname\tdatacl\ntemplate1\t{=c/operator,operator=CTc/operator}\ntemplate0\t{=c/operator,operator=CTc/operator}\npostgres\t{operator=CTc/operator,backup=c/operator,replication=CTc/operator,rewind=CTc/operator,monitoring=CTc/operator}\n"
            result = check_for_database_changes(run_cmd, unit, charm_dir, None)
            assert result == _subprocess.check_output.return_value
            _subprocess.run.assert_not_called()

            # Test when the databases changed.
            _subprocess.check_output.return_value = b"datname\tdatacl\ntemplate1\t{=c/operator,operator=CTc/operator}\ntemplate0\t{=c/operator,operator=CTc/operator}\npostgres\t{operator=CTc/operator,backup=c/operator,replication=CTc/operator,rewind=CTc/operator,monitoring=CTc/operator}\npgbouncer\t{charmed_databases_owner=Tc/charmed_databases_owner,pgbouncer_admin=c/charmed_databases_owner}\n"
            result = check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == _subprocess.check_output.return_value
            _subprocess.run.assert_called_once_with([
                run_cmd,
                "-u",
                unit,
                f"JUJU_DISPATCH_PATH=hooks/databases_change {charm_dir}/dispatch",
            ])

            # Test when the databases haven't changed.
            _subprocess.reset_mock()
            result = check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == _subprocess.check_output.return_value
            _subprocess.run.assert_not_called()
