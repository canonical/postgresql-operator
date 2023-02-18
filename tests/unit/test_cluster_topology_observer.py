# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import signal
import unittest
from typing import Optional
from unittest.mock import Mock, PropertyMock, patch

from ops.charm import CharmBase
from ops.model import ActiveStatus, Relation, WaitingStatus
from ops.testing import Harness

from cluster import Patroni
from cluster_topology_observer import (
    ClusterTopologyChangeCharmEvents,
    ClusterTopologyObserver,
    dispatch,
)


# This method will be used by the mock to replace requests.get
def mocked_requests_get(*args, **kwargs):
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data

        def json(self):
            return self.json_data

    data = {
        "http://server1/cluster": {
            "members": [{"name": "postgresql-0", "host": "1.1.1.1", "role": "leader", "lag": "1"}]
        }
    }
    if args[0] in data:
        return MockResponse(data[args[0]])


class MockCharm(CharmBase):
    on = ClusterTopologyChangeCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        self.observer = ClusterTopologyObserver(self)
        self.framework.observe(self.on.cluster_topology_change, self._on_cluster_topology_change)

    def _on_cluster_topology_change(self, _) -> None:
        self.unit.status = ActiveStatus("cluster topology changed")

    @property
    def _patroni(self) -> Patroni:
        return Mock(_patroni_url="http://1.1.1.1:8008/", verify=True)

    @property
    def _peers(self) -> Optional[Relation]:
        return None


class TestClusterTopologyChange(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness(MockCharm, meta="name: test-charm")
        self.harness.begin()
        self.charm = self.harness.charm

    @patch("builtins.open")
    @patch("subprocess.Popen")
    @patch.object(MockCharm, "_peers", new_callable=PropertyMock)
    def test_start_observer(self, _peers, _popen, _open):
        # Test that nothing is done if there is already a running process.
        _peers.return_value = Mock(data={self.charm.unit: {"observer-pid": "1"}})
        self.charm.observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if the charm is not in an active status.
        self.charm.unit.status = WaitingStatus()
        _peers.return_value = Mock(data={self.charm.unit: {}})
        self.charm.observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if peer relation is not available yet.
        self.charm.unit.status = ActiveStatus()
        _peers.return_value = None
        self.charm.observer.start_observer()
        _popen.assert_not_called()

        # Test that nothing is done if there is already a running process.
        _peers.return_value = Mock(data={self.charm.unit: {}})
        _popen.return_value = Mock(pid=1)
        self.charm.observer.start_observer()
        _popen.assert_called_once()

    @patch("os.kill")
    @patch.object(MockCharm, "_peers", new_callable=PropertyMock)
    def test_stop_observer(self, _peers, _kill):
        # Test that nothing is done if there is no process running.
        self.charm.observer.stop_observer()
        _kill.assert_not_called()

        _peers.return_value = Mock(data={self.charm.unit: {}})
        self.charm.observer.stop_observer()
        _kill.assert_not_called()

        # Test that the process is killed.
        _peers.return_value = Mock(data={self.charm.unit: {"observer-pid": "1"}})
        self.charm.observer.stop_observer()
        _kill.assert_called_once_with(1, signal.SIGINT)

    @patch("subprocess.run")
    def test_dispatch(self, _run):
        command = "test-command"
        charm_dir = "/path"
        dispatch(command, self.charm.unit.name, charm_dir, "cluster_topology_change")
        _run.assert_called_once_with(
            [
                command,
                "-u",
                self.charm.unit.name,
                f"JUJU_DISPATCH_PATH=hooks/cluster_topology_change {charm_dir}/dispatch",
            ]
        )
