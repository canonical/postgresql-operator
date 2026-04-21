# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Raft controller management for PostgreSQL watcher.

This module manages a Patroni raft_controller node that participates in
consensus without running PostgreSQL, providing the necessary third vote
for quorum in 2-node PostgreSQL clusters.

Uses Patroni's own ``patroni_raft_controller`` from the charmed-postgresql
snap, which is the same battle-tested Raft implementation used by the
PostgreSQL nodes. This guarantees wire compatibility with Patroni's
KVStoreTTL class.

The Raft service runs as a systemd service to ensure it persists between
charm hook invocations.
"""

import logging
from ipaddress import IPv4Address
from shutil import rmtree
from typing import TYPE_CHECKING, TypedDict

from charmlibs.systemd import (
    SystemdError,
    daemon_reload,
    service_disable,
    service_enable,
    service_restart,
    service_running,
    service_start,
    service_stop,
)
from jinja2 import Template
from pysyncobj.utility import TcpUtility

from utils import create_directory, render_file

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

# Base directory for all Raft instances.
# Must be under the snap's common path so that
# charmed-postgresql.patroni-raft-controller can access it.
RAFT_BASE_DIR = "/var/snap/charmed-postgresql/common/watcher-raft"
SERVICE_FILE = "/etc/systemd/system/watcher-raft@.service"


class ClusterStatus(TypedDict):
    """Type definition for the cluster status mapping."""

    running: bool
    connected: bool
    has_quorum: bool
    leader: str | None
    members: list[str]


def install_service() -> bool:
    """Install the systemd template service for the Raft controller.

    Returns:
        True if the service file was updated, False if unchanged.
    """
    with open("templates/watcher.service.j2") as file:
        template = Template(file.read())

    rendered = template.render(config_file=RAFT_BASE_DIR)
    render_file(SERVICE_FILE, rendered, 0o644, change_owner=False)

    # Reload systemd to pick up the new service
    try:
        daemon_reload()
        logger.info(f"Installed systemd service {SERVICE_FILE}")
    except SystemdError as e:
        logger.error(f"Failed to reload systemd: {e}")
        return False

    return True


class RaftController:
    """Manages the Raft service for consensus participation.

    The Raft service runs as a systemd service to ensure it persists
    between charm hook invocations. This is necessary because:
    1. Each hook invocation creates a new Python process
    2. pysyncobj requires a persistent process for Raft consensus
    3. The systemd service ensures the Raft node stays running
    """

    def __init__(self, charm: "PostgresqlOperatorCharm", instance_id: str = "default"):
        """Initialize the Raft controller.

        Args:
            charm: The PostgreSQL watcher charm instance.
            instance_id: Unique identifier for this Raft instance. Used to
                derive data directories, config files, and service names.
                Defaults to "default" for backward compatibility.

        """
        self.charm = charm
        self.instance_id = instance_id

        # Derive all paths from instance_id
        self.data_dir = f"{RAFT_BASE_DIR}/{instance_id}"
        self.config_file = f"{RAFT_BASE_DIR}/{instance_id}/patroni-raft.yaml"
        self.service_name = f"watcher-raft@{instance_id}"

    def configure(
        self,
        self_port: int,
        self_addr: str | None = None,
        partner_addrs: list[str] | None = None,
        password: str | None = None,
    ) -> bool:
        """Configure the Raft controller.

        Args:
            self_port: This node's Raft port.
            self_addr: This node's Raft address.
            partner_addrs: List of partner Raft addresses.
            password: Raft cluster password.

        Returns:
            True if configuration changed, False if unchanged.
        """
        if not partner_addrs:
            partner_addrs = []

        # Ensure data directory exists
        create_directory(self.data_dir, 0o700)
        create_directory(f"{self.data_dir}/raft", 0o700)

        if not self_addr or not password:
            logger.warning("Cannot install service: not configured")
            return False

        # Validate addresses to prevent injection into the systemd unit file
        try:
            IPv4Address(self_addr)
        except Exception:
            logger.error(f"Invalid self_addr format: {self_addr}")
            return False
        try:
            for addr in partner_addrs:
                IPv4Address(addr)
        except Exception:
            logger.error(f"Invalid partner address format: {addr}")
            return False

        with open("templates/watcher.yml.j2") as file:
            template = Template(file.read())

        # Write Patroni-compatible YAML config (includes password)
        rendered = template.render(
            self_addr=self_addr,
            self_port=self_port,
            partner_addrs=partner_addrs,
            password=password,
            data_dir=self.data_dir,
        )
        render_file(self.config_file, rendered, 0o600)

        logger.info(f"Raft controller configured: self={self_addr}, partners={partner_addrs}")
        return True

    def start(self) -> bool:
        """Start the Raft controller service.

        Returns:
            True if started successfully, False otherwise.
        """
        if service_running(self.service_name):
            logger.debug("Raft controller already running")
            return True

        try:
            # Enable and start the service
            service_enable(self.service_name)
            service_start(self.service_name)
            logger.info(f"Started Raft controller service {self.service_name}")
            return True
        except SystemdError as e:
            logger.error(f"Failed to start Raft controller: {e}")
            return False

    def stop(self) -> bool:
        """Stop the Raft controller service.

        Returns:
            True if stopped successfully, False otherwise.
        """
        if not service_running(self.service_name):
            logger.debug("Raft controller not running")
            return True

        try:
            service_stop(self.service_name)
            logger.info(f"Stopped Raft controller service {self.service_name}")
            return True
        except SystemdError as e:
            logger.error(f"Failed to stop Raft controller: {e}")
            return False

    def remove_service(self) -> bool:
        """Disable and remove the Raft systemd service unit file."""
        if not self.stop():
            return False

        try:
            service_disable(self.service_name)
        except SystemdError as e:
            logger.error(f"Failed to disable Raft controller service: {e}")
            return False

        try:
            rmtree(self.data_dir)
        except Exception as e:
            logger.error(f"Failed to remove Raft controller directory: {e}")
            return False

        return True

    def restart(self) -> bool:
        """Restart the Raft controller service.

        Returns:
            True if restarted successfully, False otherwise.
        """
        try:
            service_restart(self.service_name)
            logger.info(f"Restarted Raft controller service {self.service_name}")
            return True
        except SystemdError as e:
            logger.error(f"Failed to restart Raft controller: {e}")
            return False

    def get_status(self, self_port: int, password: str | None) -> ClusterStatus:
        """Get the Raft controller status.

        Returns:
            Dictionary with status information.
        """
        is_running = service_running(self.service_name)
        status: ClusterStatus = {
            "running": is_running,
            "connected": False,
            "has_quorum": False,
            "leader": None,
            "members": [],
        }

        if not password or not is_running:
            return status

        # Query Raft status using pysyncobj TcpUtility
        try:
            utility = TcpUtility(password=password, timeout=3)
            raft_status = utility.executeCommand(f"localhost:{self_port}", ["status"])
            status["connected"] = True
            status["has_quorum"] = raft_status.get("has_quorum", False)
            status["leader"] = (
                str(raft_status.get("leader")) if raft_status.get("leader") else None
            )

            # Extract member addresses from partner_node_status_server_* keys
            prefix = "partner_node_status_server_"
            members: list[str] = [raft_status["self"]]
            for key in raft_status:
                if isinstance(key, str) and key.startswith(prefix):
                    members.append(key[len(prefix) :])
            status["members"] = sorted(members)
            return status
        except Exception as e:
            logger.debug(f"Error querying Raft status via TcpUtility: {e}")

        return status
