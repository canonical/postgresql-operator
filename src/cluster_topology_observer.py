# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import signal
import subprocess
import sys
from time import sleep

import requests
from ops.charm import CharmBase, CharmEvents
from ops.framework import EventBase, EventSource, Object

from constants import API_REQUEST_TIMEOUT, PATRONI_CLUSTER_STATUS_ENDPOINT

logger = logging.getLogger(__name__)

# File path for the spawned cluster topology observer process to write logs.
LOG_FILE_PATH = "/var/log/cluster_topology_observer.log"


class ClusterTopologyChangeEvent(EventBase):
    """A custom event for metrics endpoint changes."""


class ClusterTopologyChangeCharmEvents(CharmEvents):
    """A CharmEvents extension for metrics endpoint changes.

    Includes :class:`MetricsEndpointChangeEvent` in those that can be handled.
    """

    cluster_topology_change = EventSource(ClusterTopologyChangeEvent)


class ClusterTopologyObserver(Object):
    """Observes changing metrics endpoints in the cluster.

    Observed endpoint changes cause :class"`MetricsEndpointChangeEvent` to be emitted.
    """

    def __init__(self, charm: CharmBase):
        """Constructor for MetricsEndpointObserver.

        Args:
            charm: the charm that is instantiating the library.
        """
        super().__init__(charm, "cluster-topology-observer")

        self._charm = charm
        self._observer_pid = 0

    def start_observer(self):
        """Start the metrics endpoint observer running in a new process."""
        self.stop_observer()

        logging.info("Starting cluster topology observer process")

        # We need to trick Juju into thinking that we are not running
        # in a hook context, as Juju will disallow use of juju-run.
        new_env = os.environ.copy()
        if "JUJU_CONTEXT_ID" in new_env:
            new_env.pop("JUJU_CONTEXT_ID")

        pid = subprocess.Popen(
            [
                "/usr/bin/python3",
                "src/cluster_topology_observer.py",
                self._charm._patroni._patroni_url,
                self._charm._patroni.verify,
                "/var/lib/juju/tools/{}/juju-run".format(self.unit_tag),
                self._charm.unit.name,
                self._charm.charm_dir,
            ],
            stdout=open(LOG_FILE_PATH, "a"),
            stderr=subprocess.STDOUT,
            env=new_env,
        ).pid

        self._observer_pid = pid
        logging.info("Started metrics endopint observer process with PID {}".format(pid))

    def stop_observer(self):
        """Stop the running observer process if we have previously started it."""
        if not self._observer_pid:
            return

        try:
            os.kill(self._observer_pid, signal.SIGINT)
            msg = "Stopped running cluster topology observer process with PID {}"
            logging.info(msg.format(self._observer_pid))
        except OSError:
            pass

    @property
    def unit_tag(self):
        """Juju-style tag identifying the unit being run by this charm."""
        unit_num = self._charm.unit.name.split("/")[-1]
        return "unit-{}-{}".format(self._charm.app.name, unit_num)


def dispatch(run_cmd, unit, charm_dir):
    """Use the input juju-run command to dispatch a :class:`ClusterTopologyChangeEvent`."""
    dispatch_sub_cmd = "JUJU_DISPATCH_PATH=hooks/cluster_topology_change {}/dispatch"
    subprocess.run([run_cmd, "-u", unit, dispatch_sub_cmd.format(charm_dir)])


def main():
    """Main watch and dispatch loop.

    Watch the input k8s service names. When changes are detected, write the
    observed data to the payload file, and dispatch the change event.
    """
    patroni_url, verify, run_cmd, unit, charm_dir = sys.argv[1:]

    previous_cluster_topology = {}
    while True:
        cluster_status = requests.get(
            f"{patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
            verify=verify,
            timeout=API_REQUEST_TIMEOUT,
        )
        current_cluster_topology = {
            member["name"]: member["role"] for member in cluster_status.json()["members"]
        }
        if not previous_cluster_topology:
            previous_cluster_topology = current_cluster_topology
        elif current_cluster_topology != previous_cluster_topology:
            dispatch(run_cmd, unit, charm_dir)
        sleep(10)


if __name__ == "__main__":
    main()
