# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch, sentinel

import pytest
from ops.testing import Harness
from pysyncobj.utility import UtilityException
from single_kernel_postgresql.config.exceptions import (
    AddRaftMemberFailedError,
    RemoveRaftMemberFailedError,
)
from tenacity import wait_fixed

from charm import PostgresqlOperatorCharm
from cluster import Patroni
from constants import PATRONI_CONF_PATH, RAFT_PARTNER_PREFIX

PATRONI_SERVICE = "patroni"
CREATE_CLUSTER_CONF_PATH = "/var/snap/charmed-postgresql/current/etc/postgresql/postgresql.conf"


@pytest.fixture()
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    yield harness
    harness.cleanup()


@pytest.fixture(autouse=True)
def patroni(harness):
    patroni = Patroni(harness.charm, "fake-raft-password")
    yield patroni


def test_cleanup_raft_cluster(patroni):
    with (
        patch("cluster.TcpUtility") as _tcp_utility,
        patch("cluster.Patroni.remove_raft_member", return_value=True) as _remove_raft_member,
        patch(
            "charm.PostgresqlOperatorCharm._units_ips",
            new_callable=PropertyMock,
            return_value={"1.1.1.1"},
        ),
        patch(
            "charm.PostgresqlOperatorCharm._remove_from_members_ips"
        ) as _remove_from_members_ips,
        patch("charm.VMWorkload.is_patroni_running", return_value=True),
    ):
        # Error connecting to raft
        _tcp_utility.side_effect = Exception

        assert not patroni.cleanup_raft_cluster()

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        _tcp_utility.reset_mock()

        # No status
        _tcp_utility.side_effect = None
        _tcp_utility.return_value.executeCommand.return_value = {}

        assert not patroni.cleanup_raft_cluster()

        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )

        # All members active
        _tcp_utility.return_value.executeCommand.return_value = {
            f"{RAFT_PARTNER_PREFIX}1.1.1.1:2222": 2
        }

        assert patroni.cleanup_raft_cluster()

        assert not _remove_raft_member.called

        # Filter by unit ips
        _tcp_utility.return_value.executeCommand.return_value = {
            f"{RAFT_PARTNER_PREFIX}1.1.1.1:2222": 0,
            f"{RAFT_PARTNER_PREFIX}2.2.2.2:2222": 0,
        }

        assert patroni.cleanup_raft_cluster()

        _remove_raft_member.assert_called_once_with("2.2.2.2:2222")
        _remove_from_members_ips.assert_called_once_with("2.2.2.2")


def test_add_raft_member(patroni):
    with patch("cluster.TcpUtility") as _tcp_utility:
        # Member already removed
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": True,
            "leader": sentinel.raft_leader,
        }

        patroni.add_raft_member("1.2.3.4:2222")

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )
        _tcp_utility.reset_mock()

        # Add member
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            "SUCCESS",
        ]

        patroni.add_raft_member("1.2.3.4:2222")

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        assert _tcp_utility.return_value.executeCommand.call_count == 2
        _tcp_utility.return_value.executeCommand.assert_any_call("127.0.0.1:2222", ["status"])
        _tcp_utility.return_value.executeCommand.assert_any_call(
            "127.0.0.1:2222", ["add", "1.2.3.4:2222"]
        )
        _tcp_utility.reset_mock()

        # Raises on failed status
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            "FAIL",
        ]

        with pytest.raises(AddRaftMemberFailedError):
            patroni.add_raft_member("1.2.3.4:2222")
            assert False

        # Raises on add error
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            UtilityException,
        ]

        with pytest.raises(AddRaftMemberFailedError):
            patroni.add_raft_member("1.2.3.4:2222")
            assert False

        # Raises on status error
        _tcp_utility.return_value.executeCommand.side_effect = [UtilityException]

        with pytest.raises(AddRaftMemberFailedError):
            patroni.add_raft_member("1.2.3.4:2222")
            assert False


def test_remove_raft_member(patroni):
    with patch("cluster.TcpUtility") as _tcp_utility:
        # Member already removed
        _tcp_utility.return_value.executeCommand.return_value = "Response message"

        patroni.remove_raft_member("1.2.3.4:2222")

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )
        _tcp_utility.reset_mock()

        # Removing member
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "partner_node_status_server_1.2.3.4:2222": 0,
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            "SUCCESS",
        ]

        patroni.remove_raft_member("1.2.3.4:2222")

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        assert _tcp_utility.return_value.executeCommand.call_count == 2
        _tcp_utility.return_value.executeCommand.assert_any_call("127.0.0.1:2222", ["status"])
        _tcp_utility.return_value.executeCommand.assert_any_call(
            "127.0.0.1:2222", ["remove", "1.2.3.4:2222"]
        )
        _tcp_utility.reset_mock()

        # Raises on failed status
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "partner_node_status_server_1.2.3.4:2222": 0,
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            "FAIL",
        ]

        with pytest.raises(RemoveRaftMemberFailedError):
            patroni.remove_raft_member("1.2.3.4:2222")
            assert False

        # Raises on remove error
        _tcp_utility.return_value.executeCommand.side_effect = [
            {
                "partner_node_status_server_1.2.3.4:2222": 0,
                "has_quorum": True,
                "leader": sentinel.raft_leader,
            },
            UtilityException,
        ]

        with pytest.raises(RemoveRaftMemberFailedError):
            patroni.remove_raft_member("1.2.3.4:2222")
            assert False

        # Raises on status error
        _tcp_utility.return_value.executeCommand.side_effect = [UtilityException]

        with pytest.raises(RemoveRaftMemberFailedError):
            patroni.remove_raft_member("1.2.3.4:2222")
            assert False


def test_remove_raft_member_no_quorum(patroni, harness):
    with (
        patch("cluster.TcpUtility") as _tcp_utility,
        patch(
            "charm.PostgresqlOperatorCharm.unit_peer_data", new_callable=PropertyMock
        ) as _unit_peer_data,
    ):
        # Async replica
        _unit_peer_data.return_value = {}
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": False,
            "leader": None,
        }

        patroni.remove_raft_member("1.2.3.4:2222")
        assert harness.charm.unit_peer_data == {"raft_stuck": "True"}

        # No health
        _unit_peer_data.return_value = {}
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": False,
            "leader": None,
        }

        patroni.remove_raft_member("1.2.3.4:2222")

        assert harness.charm.unit_peer_data == {"raft_stuck": "True"}

        # Sync replica
        _unit_peer_data.return_value = {}
        leader_mock = Mock()
        leader_mock.address = "1.2.3.4:2222"
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": False,
            "leader": leader_mock,
        }

        patroni.remove_raft_member("1.2.3.4:2222")

        assert harness.charm.unit_peer_data == {"raft_stuck": "True"}


def test_remove_raft_data(patroni):
    with (
        patch("charm.PatroniManager.stop_patroni") as _stop_patroni,
        patch("cluster.psutil") as _psutil,
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
        patch("shutil.rmtree") as _rmtree,
        patch("pathlib.Path.is_dir") as _is_dir,
        patch("pathlib.Path.exists") as _exists,
    ):
        mock_proc_pg = Mock()
        mock_proc_not_pg = Mock()
        mock_proc_pg.name.return_value = "postgres"
        mock_proc_not_pg.name.return_value = "something_else"
        _psutil.process_iter.side_effect = [[mock_proc_not_pg, mock_proc_pg], [mock_proc_not_pg]]

        patroni.remove_raft_data()

        _stop_patroni.assert_called_once_with()
        assert _psutil.process_iter.call_count == 2
        _psutil.process_iter.assert_any_call(["name"])
        _rmtree.assert_called_once_with(Path(f"{PATRONI_CONF_PATH}/raft"))


def test_reinitialise_raft_data(patroni):
    with (
        patch("charm.PatroniManager.get_patroni_health") as _get_patroni_health,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PatroniManager.start_patroni") as _start_patroni,
        patch("charm.PatroniManager.restart_patroni") as _restart_patroni,
        patch("cluster.psutil") as _psutil,
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
    ):
        mock_proc_pg = Mock()
        mock_proc_not_pg = Mock()
        mock_proc_pg.name.return_value = "postgres"
        mock_proc_not_pg.name.return_value = "something_else"
        _psutil.process_iter.side_effect = [[mock_proc_not_pg], [mock_proc_not_pg, mock_proc_pg]]
        _get_patroni_health.side_effect = [
            {"role": "replica", "state": "streaming"},
            {"role": "leader", "state": "running"},
        ]

        patroni.reinitialise_raft_data()

        _update_config.assert_called_once_with(no_peers=True)
        _start_patroni.assert_called_once_with()
        _restart_patroni.assert_called_once_with()
        assert _psutil.process_iter.call_count == 2
        _psutil.process_iter.assert_any_call(["name"])
