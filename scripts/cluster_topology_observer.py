# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cluster topology changes observer."""

import json
import ssl
import subprocess
import sys
from time import sleep
from urllib.request import urlopen

from constants import API_REQUEST_TIMEOUT, PATRONI_CLUSTER_STATUS_ENDPOINT

# File path for the spawned cluster topology observer process to write logs.
LOG_FILE_PATH = "/var/log/cluster_topology_observer.log"

ssl._create_default_https_context = ssl._create_unverified_context


def dispatch(run_cmd, unit, charm_dir):
    """Use the input juju-run command to dispatch a :class:`ClusterTopologyChangeEvent`."""
    dispatch_sub_cmd = "JUJU_DISPATCH_PATH=hooks/cluster_topology_change {}/dispatch"
    # Input is generated by the charm
    subprocess.run([run_cmd, "-u", unit, dispatch_sub_cmd.format(charm_dir)])  # noqa: S603


def main():
    """Main watch and dispatch loop.

    Watch the Patroni API cluster info. When changes are detected, dispatch the change event.
    """
    patroni_url, run_cmd, unit, charm_dir = sys.argv[1:]

    previous_cluster_topology = {}
    while True:
        try:
            # Scheme is generated by the charm
            resp = urlopen(  # noqa: S310
                f"{patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}", timeout=API_REQUEST_TIMEOUT
            )
            cluster_status = json.loads(resp.read())
        except Exception as e:
            print(f"Failed to get cluster status {e}")
            sleep(30)
            continue
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
