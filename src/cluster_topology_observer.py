# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cluster topology changes observer."""

import logging
import os
import signal
import subprocess
import sys
from time import sleep

import requests
from ops.charm import CharmBase, CharmEvents
from ops.framework import EventBase, EventSource, Object
from ops.model import ActiveStatus

from constants import API_REQUEST_TIMEOUT, PATRONI_CLUSTER_STATUS_ENDPOINT

logger = logging.getLogger(__name__)

# File path for the spawned cluster topology observer process to write logs.
LOG_FILE_PATH = "/var/log/cluster_topology_observer.log"


class ClusterTopologyChangeEvent(EventBase):
    """A custom event for cluster topology changes."""


class ClusterTopologyChangeCharmEvents(CharmEvents):
    """A CharmEvents extension for cluster topology changes.

    Includes :class:`ClusterTopologyChangeEvent` in those that can be handled.
    """

    cluster_topology_change = EventSource(ClusterTopologyChangeEvent)


class ClusterTopologyObserver(Object):
    """Observes changing topology in the cluster.

    Observed cluster topology changes cause :class"`ClusterTopologyChangeEvent` to be emitted.
    """

    def __init__(self, charm: CharmBase, run_cmd: str):
        """Constructor for ClusterTopologyObserver.

        Args:
            charm: the charm that is instantiating the library.
            run_cmd: run command to use to dispatch events.
        """
        super().__init__(charm, "cluster-topology-observer")

        self._charm = charm
        self._run_cmd = run_cmd

    def restart_observer(self):
        """Restart the cluster topology observer process."""
        self.stop_observer()
        self.start_observer(skip_status_check=True)

    def start_observer(self, skip_status_check: bool = False):
        """Start the cluster topology observer running in a new process."""
        if not skip_status_check and (
            not isinstance(self._charm.unit.status, ActiveStatus) or self._charm._peers is None
        ):
            return
        if "observer-pid" in self._charm._peers.data[self._charm.unit]:
            # Double check that the PID exists
            pid = int(self._charm._peers.data[self._charm.unit]["observer-pid"])
            try:
                os.kill(pid, 0)
                return
            except OSError:
                pass

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
                ",".join([
                    self._charm._patroni._get_alternative_patroni_url(number)
                    for number in range(2 * len(self._charm._peer_members_ips) + 1)[1:]
                ]),
                f"{self._charm._patroni.verify}",
                self._run_cmd,
                self._charm.unit.name,
                self._charm.charm_dir,
            ],
            stdout=open(LOG_FILE_PATH, "a"),
            stderr=subprocess.STDOUT,
            env=new_env,
        ).pid

        self._charm._peers.data[self._charm.unit].update({"observer-pid": f"{pid}"})
        logging.info("Started cluster topology observer process with PID {}".format(pid))

    def stop_observer(self):
        """Stop the running observer process if we have previously started it."""
        if (
            self._charm._peers is None
            or "observer-pid" not in self._charm._peers.data[self._charm.unit]
        ):
            return

        observer_pid = int(self._charm._peers.data[self._charm.unit].get("observer-pid"))

        try:
            os.kill(observer_pid, signal.SIGINT)
            msg = "Stopped running cluster topology observer process with PID {}"
            logging.info(msg.format(observer_pid))
            self._charm._peers.data[self._charm.unit].update({"observer-pid": ""})
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

    Watch the Patroni API cluster info. When changes are detected, dispatch the change event.
    """
    patroni_url, alternative_patroni_urls, verify, run_cmd, unit, charm_dir = sys.argv[1:]

    previous_cluster_topology = {}
    urls = [patroni_url] + list(filter(None, alternative_patroni_urls.split(",")))
    while True:
        for url in urls:
            try:
                cluster_status = requests.get(
                    f"{url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                    verify=verify,
                    timeout=API_REQUEST_TIMEOUT,
                )
            except Exception as e:
                with open(LOG_FILE_PATH, "a") as log_file:
                    log_file.write(
                        f"Failed to get cluster status when using {url}: {e} - {type(e)}\n"
                    )
                if url == urls[-1]:
                    with open(LOG_FILE_PATH, "a") as log_file:
                        log_file.write("No more peers to try to get the cluster status from.\n")
                    break
                else:
                    continue
            else:
                current_cluster_topology = {
                    member["name"]: member["role"] for member in cluster_status.json()["members"]
                }

                # If it's the first time the cluster topology was retrieved, then store it and use
                # it for subsequent checks.
                if not previous_cluster_topology:
                    previous_cluster_topology = current_cluster_topology
                # If the cluster topology changed, dispatch a charm event to handle this change.
                elif current_cluster_topology != previous_cluster_topology:
                    previous_cluster_topology = current_cluster_topology
                    dispatch(run_cmd, unit, charm_dir)

                # Wait some time before checking again for a cluster topology change.
                sleep(30)


if __name__ == "__main__":
    main()
