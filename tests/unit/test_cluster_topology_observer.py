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
            "single_kernel_postgresql.core.state.CharmState.unit_ip", new_callable=PropertyMock
        ) as _unit_ip,
    ):
        harness.charm.unit.status = ActiveStatus()
        _unit_ip.return_value = "1.2.3.4"

        # Test that nothing is done if the PID is already recorded and alive.
        with patch("single_kernel_postgresql.managers.observer.os.kill") as _kill:
            harness.charm.state.peer.observer_pid = 1
            harness.charm.observer_manager.start_observer()
            _popen.assert_not_called()
            _kill.assert_called_once_with(1, 0)
        harness.charm.state.peer.observer_pid = None

        # Test that nothing is done if the charm is not in an active status.
        harness.charm.unit.status = WaitingStatus()
        harness.charm.observer_manager.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if there is no unit IP yet.
        harness.charm.unit.status = ActiveStatus()
        _unit_ip.return_value = None
        harness.charm.observer_manager.start_observer()
        _popen.assert_not_called()

        # Happy path: no PID, active, IP set -> the process spawns.
        _unit_ip.return_value = "1.2.3.4"
        _popen.return_value = Mock(pid=1)
        harness.charm.observer_manager.start_observer()
        _popen.assert_called_once()


