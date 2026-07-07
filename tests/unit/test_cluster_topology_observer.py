# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import signal
import sys
from unittest.mock import Mock, PropertyMock, mock_open, patch, sentinel

import pytest
from jinja2 import Template
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import Harness
from pysyncobj.utility import UtilityException
from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.literals import PEER_RELATION

from charm import PostgresqlOperatorCharm
from cluster_topology_observer import start_raft_observer
from scripts.cluster_topology_observer import (
    UnreachableUnitsError,
    check_for_database_changes,
    dispatch,
    main,
)
from scripts.raft_observer import check_raft_connection


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.add_relation(PEER_RELATION, "postgresql")
    harness.begin()
    yield harness
    harness.cleanup()


def test_start_observer(harness):
    with (
        patch("builtins.open") as _open,
        patch("subprocess.Popen") as _popen,
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLPeer.data",
            new_callable=PropertyMock,
        ) as _peer_data,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip", new_callable=PropertyMock),
        patch(
            "charm.PostgresqlOperatorCharm._peer_members_ips", new_callable=PropertyMock
        ) as _peer_members_ips,
        patch(
            "charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=True
        ) as _peers,
    ):
        # Test that nothing is done if there is already a running process.
        _peer_data.return_value = {"observer-pid": "1"}
        harness.charm._observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if the charm is not in an active status.
        harness.charm.unit.status = WaitingStatus()
        _peer_data.return_value = {}
        harness.charm._observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if peer relation is not available yet.
        harness.charm.unit.status = ActiveStatus()
        _peers.return_value = None
        harness.charm._observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if there is already a running process.
        _peers.return_value = True
        _peer_data.return_value = {harness.charm.unit: {}}
        _popen.return_value = Mock(pid=1)
        harness.charm._observer.start_observer()
        _popen.assert_called_once()


def test_start_observer_already_running(harness):
    with (
        patch("builtins.open") as _open,
        patch("subprocess.Popen") as _popen,
        patch("os.kill") as _kill,
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLPeer.data",
            new_callable=PropertyMock,
        ) as _peer_data,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip", new_callable=PropertyMock),
        patch(
            "charm.PostgresqlOperatorCharm._peer_members_ips", new_callable=PropertyMock
        ) as _peer_members_ips,
        patch(
            "charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=True
        ),
    ):
        harness.charm.unit.status = ActiveStatus()
        _peer_data.return_value = {"observer-pid": "1234"}
        harness.charm._observer.start_observer()
        _kill.assert_called_once_with(1234, 0)
        assert not _popen.called
        _kill.reset_mock()

        # If process is already dead, it should restart
        _kill.side_effect = OSError
        harness.charm._observer.start_observer()
        _kill.assert_called_once_with(1234, 0)
        _popen.assert_called_once()
        _kill.reset_mock()


def test_stop_observer(harness):
    with (
        patch("os.kill") as _kill,
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLPeer.data",
            new_callable=PropertyMock,
        ) as _peer_data,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip", new_callable=PropertyMock),
        patch(
            "charm.PostgresqlOperatorCharm._peer_members_ips", new_callable=PropertyMock
        ) as _peer_members_ips,
        patch(
            "charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=True
        ),
    ):
        # Test that nothing is done if there is no process running.
        harness.charm._observer.stop_observer()
        _kill.assert_not_called()

        _peer_data.return_value = {}
        harness.charm._observer.stop_observer()
        _kill.assert_not_called()

        # Test that the process is killed.
        _peer_data.return_value = {"observer-pid": "1"}
        harness.charm._observer.stop_observer()
        _kill.assert_called_once_with(1, signal.SIGINT)
        _kill.reset_mock()

        # Dead process doesn't break the script
        _peer_data.return_value = {"observer-pid": "1"}
        _kill.side_effect = OSError
        harness.charm._observer.stop_observer()
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
  listen: test:5432
  authentication:
    superuser:
      username: test_user
      password: test_password"""
        )
        with patch("builtins.open", mock, create=True):
            _cursor = _psycopg2.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            _cursor.fetchall.side_effect = [[sentinel.databases], sentinel.relation_users]

            # Test the first time this function is called.
            result = check_for_database_changes(run_cmd, unit, charm_dir, None)
            assert result == [sentinel.databases, sentinel.relation_users]
            _subprocess.run.assert_not_called()
            _psycopg2.connect.assert_called_once_with(
                "dbname='postgres' user='operator' host='/tmp/snap-private-tmp/snap.charmed-postgresql/tmp/' "
                "password='test_password' connect_timeout=1"
            )
            assert _cursor.execute.call_count == 2
            _cursor.execute.assert_any_call("SELECT datname, datacl FROM pg_database;")
            _cursor.execute.assert_any_call(
                "SELECT oid, rolname FROM pg_roles WHERE pg_has_role(oid, 'relation_access', 'member');"
            )

            # Test when the databases changed.
            _cursor.fetchall.side_effect = [[sentinel.databases_changed], sentinel.relation_users]
            result = check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == [sentinel.databases_changed, sentinel.relation_users]

            _subprocess.run.assert_called_once_with([
                run_cmd,
                "-u",
                unit,
                f"JUJU_DISPATCH_PATH=hooks/databases_change {charm_dir}/dispatch",
            ])

            # Test when the databases haven't changed.
            _subprocess.reset_mock()
            _cursor.fetchall.side_effect = [[sentinel.databases_changed], sentinel.relation_users]
            check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == [sentinel.databases_changed, sentinel.relation_users]
            _subprocess.run.assert_not_called()


def test_start_raft_observer(harness):
    with (
        patch("os.getcwd", return_value="testdir"),
        patch("cluster_topology_observer.daemon_reload") as _daemon_reload,
        patch("cluster_topology_observer.service_enable") as _service_enable,
        patch("cluster_topology_observer.render_file") as _render_file,
        patch(
            "cluster_topology_observer.copy_environment", return_value={"ENV": "var"}
        ) as _copy_environment,
    ):
        # Get the expected content from a file.
        with open("templates/raft-observer.service.j2") as file:
            contents = file.read()
            template = Template(contents)
        expected_service = template.render(
            envvars={"ENV": "var"}, script="testdir/scripts/raft_observer.py"
        )
        with open("templates/raft-observer.timer.j2") as file:
            contents = file.read()
            template = Template(contents)
        expected_timer = template.render()

        start_raft_observer()

        _daemon_reload.assert_called_once_with()
        _service_enable.assert_called_once_with("/etc/systemd/system/raft-observer.timer", "--now")
        assert _render_file.call_count == 2
        _render_file.assert_any_call(
            Substrates.VM,
            "/etc/systemd/system/raft-observer.service",
            expected_service,
            0o644,
            change_owner=False,
        )
        _render_file.assert_any_call(
            Substrates.VM,
            "/etc/systemd/system/raft-observer.timer",
            expected_timer,
            0o644,
            change_owner=False,
        )


def test_check_raft_connection():
    with (
        patch("scripts.raft_observer.TcpUtility") as _tcp_utility,
        patch("scripts.raft_observer.dispatch") as _dispatch,
    ):
        # No status
        _tcp_utility.return_value.executeCommand.return_value = None
        check_raft_connection("testpass")

        _tcp_utility.assert_called_once_with(password="testpass", timeout=3)
        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )
        assert not _dispatch.called
        _tcp_utility.reset_mock()

        # No leader
        _tcp_utility.return_value.executeCommand.return_value = {
            "has_quorum": False,
            "leader": None,
        }

        check_raft_connection("testpass")

        _tcp_utility.assert_called_once_with(password="testpass", timeout=3)
        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )
        assert not _dispatch.called
        _tcp_utility.reset_mock()

        # Status exceeption
        _tcp_utility.return_value.executeCommand.side_effect = UtilityException
        check_raft_connection("testpass")

        _tcp_utility.assert_called_once_with(password="testpass", timeout=3)
        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )
        assert not _dispatch.called
        _tcp_utility.reset_mock()

        # Disconneected partner
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "partner_node_status_server_1.1.1.1:2222": 2,
                "partner_node_status_server_2.2.2.2:2222": 0,
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            UtilityException,
        ]
        check_raft_connection("testpass")

        _tcp_utility.assert_called_once_with(password="testpass", timeout=3)
        assert _tcp_utility.return_value.executeCommand.call_count == 2
        _tcp_utility.return_value.executeCommand.assert_any_call("127.0.0.1:2222", ["status"])
        _tcp_utility.return_value.executeCommand.assert_any_call("2.2.2.2:2222", ["status"])
        assert not _dispatch.called
        _tcp_utility.reset_mock()

        # Stuck partner
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "partner_node_status_server_1.1.1.1:2222": 2,
                "partner_node_status_server_2.2.2.2:2222": 0,
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            {
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
        ]
        check_raft_connection("testpass")

        _tcp_utility.assert_called_once_with(password="testpass", timeout=3)
        assert _tcp_utility.return_value.executeCommand.call_count == 2
        _tcp_utility.return_value.executeCommand.assert_any_call("127.0.0.1:2222", ["status"])
        _tcp_utility.return_value.executeCommand.assert_any_call("2.2.2.2:2222", ["status"])
        _dispatch.assert_called_once_with("raft_reconnect")
        _tcp_utility.reset_mock()
