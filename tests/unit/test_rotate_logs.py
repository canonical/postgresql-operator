# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import Dict, Optional
from unittest.mock import Mock, PropertyMock, patch

import pytest
from ops.charm import CharmBase
from ops.model import ActiveStatus, Relation, WaitingStatus
from ops.testing import Harness

from rotate_logs import RotateLogs


class MockCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.rotate_logs = RotateLogs(self)

    @property
    def _peers(self) -> Optional[Relation]:
        return None

    @property
    def unit_peer_data(self) -> Dict:
        """Unit peer relation data object."""
        if self._peers is None:
            return {}

        return self._peers.data[self.unit]


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(MockCharm, meta="name: test-charm")
    harness.begin()
    yield harness
    harness.cleanup()


def test_start_log_rotation(harness):
    with (
        patch("builtins.open") as _open,
        patch("subprocess.Popen") as _popen,
        patch("os.path.exists") as _exists,
        patch.object(MockCharm, "_peers", new_callable=PropertyMock) as _peers,
    ):
        # Test that nothing is done if there is already a running process.
        _peers.return_value = Mock(data={harness.charm.unit: {"rotate-logs-pid": "1"}})
        _exists.return_value = True
        harness.charm.rotate_logs.start_log_rotation()
        _popen.assert_not_called()

        # Test that nothing is done if the charm is not in an active status.
        harness.charm.unit.status = WaitingStatus()
        _peers.return_value = Mock(data={harness.charm.unit: {}})
        harness.charm.rotate_logs.start_log_rotation()
        _popen.assert_not_called()

        # Test that nothing is done if peer relation is not available yet.
        harness.charm.unit.status = ActiveStatus()
        _peers.return_value = None
        harness.charm.rotate_logs.start_log_rotation()
        _popen.assert_not_called()

        # Test that nothing is done if the logrotate file does not exist.
        _peers.return_value = Mock(data={harness.charm.unit: {}})
        _exists.return_value = False
        harness.charm.rotate_logs.start_log_rotation()
        _popen.assert_not_called()

        # Test that nothing is done if there is already a running process.
        _popen.return_value = Mock(pid=1)
        _exists.return_value = True
        harness.charm.rotate_logs.start_log_rotation()
        _popen.assert_called_once()


def test_start_log_rotation_already_running(harness):
    with (
        patch("builtins.open") as _open,
        patch("subprocess.Popen") as _popen,
        patch("os.kill") as _kill,
        patch("os.path.exists") as _exists,
        patch.object(MockCharm, "_peers", new_callable=PropertyMock) as _peers,
    ):
        harness.charm.unit.status = ActiveStatus()
        _peers.return_value = Mock(data={harness.charm.unit: {"rotate-logs-pid": "1234"}})
        _exists.return_value = True
        harness.charm.rotate_logs.start_log_rotation()
        _kill.assert_called_once_with(1234, 0)
        assert not _popen.called
        _kill.reset_mock()

        # If process is already dead, it should restart.
        _kill.side_effect = OSError
        harness.charm.rotate_logs.start_log_rotation()
        _kill.assert_called_once_with(1234, 0)
        _popen.assert_called_once()
