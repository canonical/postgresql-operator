# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import signal
from importlib.resources import files
from unittest.mock import Mock, PropertyMock, patch

import pytest
from jinja2 import Template
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import Harness
from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.literals import PEER_RELATION
from single_kernel_postgresql.observers.cluster_topology import start_raft_observer

from charm import PostgresqlOperatorCharm


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.add_relation(PEER_RELATION, "postgresql")
    harness.begin()
    yield harness
    harness.cleanup()


def test_start_observer(harness):
    with (
        patch("builtins.open"),
        patch("subprocess.Popen") as _popen,
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLPeer.data",
            new_callable=PropertyMock,
        ) as _peer_data,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip", new_callable=PropertyMock),
        patch(
            "single_kernel_postgresql.core.state.CharmState.peer_members_ips",
            new_callable=PropertyMock,
        ) as _peer_members_ips,
    ):
        observer = harness.charm._observer

        # Test that nothing is done if there is already a running process.
        _peer_data.return_value = {"observer-pid": "1"}
        observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if the charm is not in an active status.
        harness.charm.unit.status = WaitingStatus()
        _peer_data.return_value = {}
        observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if the peer relation is not available yet.
        harness.charm.unit.status = ActiveStatus()
        with patch(
            "single_kernel_postgresql.core.state.CharmState.peer_relation",
            new_callable=PropertyMock,
            return_value=None,
        ):
            observer.start_observer()
        _popen.assert_not_called()

        # Test that the process is started otherwise.
        _popen.return_value = Mock(pid=1)
        observer.start_observer()
        _popen.assert_called_once()


def test_start_observer_already_running(harness):
    with (
        patch("builtins.open"),
        patch("subprocess.Popen") as _popen,
        patch("os.kill") as _kill,
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLPeer.data",
            new_callable=PropertyMock,
        ) as _peer_data,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip", new_callable=PropertyMock),
        patch(
            "single_kernel_postgresql.core.state.CharmState.peer_members_ips",
            new_callable=PropertyMock,
        ) as _peer_members_ips,
    ):
        harness.charm.unit.status = ActiveStatus()
        observer = harness.charm._observer
        _peer_data.return_value = {"observer-pid": "1234"}
        observer.start_observer()
        _kill.assert_called_once_with(1234, 0)
        assert not _popen.called
        _kill.reset_mock()

        # If process is already dead, it should restart
        _kill.side_effect = OSError
        observer.start_observer()
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
    ):
        observer = harness.charm._observer

        # Test that nothing is done if there is no process running.
        observer.stop_observer()
        _kill.assert_not_called()

        _peer_data.return_value = {}
        observer.stop_observer()
        _kill.assert_not_called()

        # Test that the process is killed.
        _peer_data.return_value = {"observer-pid": "1"}
        observer.stop_observer()
        _kill.assert_called_once_with(1, signal.SIGINT)
        _kill.reset_mock()

        # Dead process doesn't break the script
        _peer_data.return_value = {"observer-pid": "1"}
        _kill.side_effect = OSError
        observer.stop_observer()
        _kill.assert_called_once_with(1, signal.SIGINT)
        _kill.reset_mock()


def test_start_raft_observer(harness):
    with (
        patch(
            "single_kernel_postgresql.observers.cluster_topology.daemon_reload"
        ) as _daemon_reload,
        patch(
            "single_kernel_postgresql.observers.cluster_topology.service_enable"
        ) as _service_enable,
        patch("single_kernel_postgresql.observers.cluster_topology.render_file") as _render_file,
        patch(
            "single_kernel_postgresql.observers.cluster_topology.copy_environment",
            return_value={"ENV": "var"},
        ) as _copy_environment,
    ):
        # Get the expected content from the library package templates.
        service_source = (
            files("single_kernel_postgresql.templates")
            .joinpath("vm/raft-observer.service.j2")
            .read_text()
        )
        expected_service = Template(service_source).render(
            envvars={"ENV": "var"},
            script=str(files("single_kernel_postgresql.scripts").joinpath("raft_observer.py")),
        )
        timer_source = (
            files("single_kernel_postgresql.templates")
            .joinpath("vm/raft-observer.timer.j2")
            .read_text()
        )
        expected_timer = Template(timer_source).render()

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
