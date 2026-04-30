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
from contextlib import suppress
from ipaddress import ip_address
from shutil import rmtree
from typing import TYPE_CHECKING, TypedDict

import psycopg2
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
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from cluster import ClusterMember
from constants import PATRONI_CLUSTER_STATUS_ENDPOINT, RAFT_PARTNER_PREFIX, RAFT_PORT
from utils import create_directory, parallel_patroni_get_request, render_file

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

# Base directory for all Raft instances.
# Must be under the snap's common path so that
# charmed-postgresql.patroni-raft-controller can access it.
RAFT_BASE_DIR = "/var/snap/charmed-postgresql/common/watcher-raft"
SERVICE_FILE = "/etc/systemd/system/watcher-raft@.service"

# Default health check configuration
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_INTERVAL_SECONDS = 7
DEFAULT_QUERY_TIMEOUT_SECONDS = 5
DEFAULT_CHECK_INTERVAL_SECONDS = 10

# TCP keepalive settings to detect dead connections quickly
TCP_KEEPALIVE_IDLE = 1  # Start keepalive probes after 1 second of idle
TCP_KEEPALIVE_INTERVAL = 1  # Send keepalive probes every 1 second
TCP_KEEPALIVE_COUNT = 3  # Consider connection dead after 3 failed probes


class ClusterStatus(TypedDict):
    """Type definition for the cluster status mapping."""

    running: bool
    connected: bool
    has_quorum: bool
    leader: str | None
    members: list[str]


def install_service() -> None:
    """Install the systemd template service for the Raft controller.

    Returns:
        True if the service file was updated, False if unchanged.
    """
    with open("templates/watcher.service.j2") as file:
        template = Template(file.read())

    rendered = template.render(config_file=RAFT_BASE_DIR)
    render_file(SERVICE_FILE, rendered, 0o644, change_owner=False)

    # Reload systemd to pick up the new service
    daemon_reload()
    logger.info(f"Installed systemd service {SERVICE_FILE}")


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
        self.ca_file = f"{RAFT_BASE_DIR}/{instance_id}/patroni-ca.pem"
        self.service_name = f"watcher-raft@{instance_id}"

    def configure(
        self,
        self_port: int,
        self_addr: str | None = None,
        partner_addrs: list[str] | None = None,
        password: str | None = None,
        cas: str | None = None,
    ) -> bool:
        """Configure the Raft controller.

        Args:
            self_port: This node's Raft port.
            self_addr: This node's Raft address.
            partner_addrs: List of partner Raft addresses.
            password: Raft cluster password.
            cas: Patroni CA bundle.

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
            ip_address(self_addr)
        except Exception:
            logger.error(f"Invalid self_addr format: {self_addr}")
            return False
        try:
            for addr in partner_addrs:
                ip_address(addr)
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
        if cas:
            render_file(self.ca_file, cas, 0o600)

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
            with suppress(FileNotFoundError):
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
            service_enable(self.service_name)
            service_restart(self.service_name)
            logger.info(f"Restarted Raft controller service {self.service_name}")
            return True
        except SystemdError as e:
            logger.error(f"Failed to restart Raft controller: {e}")
            return False

    def get_stale_watchers(
        self, member_address: str, raft_password: str, partner_addrs: list[str], port: int
    ) -> list[str]:
        """Collect stale watcher raft members."""
        port_postfix = str(port)
        watcher_addr = f"{member_address}:{port}"
        watcher_key = f"{RAFT_PARTNER_PREFIX}{watcher_addr}"

        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=raft_password, timeout=3)

        stale_addrs = []
        addrs = [watcher_addr, *[f"{addr}:{RAFT_PORT}" for addr in partner_addrs]]
        for raft_host in addrs:
            try:
                raft_status = syncobj_util.executeCommand(raft_host, ["status"])
            except Exception as e:
                logger.warning(f"Collect stale addrs: Cannot connect to raft cluster: {e}")
                continue
            if not raft_status:
                logger.warning("Collect stale addrs: No raft status")
                continue
            for key in raft_status:
                if (
                    key.startswith(RAFT_PARTNER_PREFIX)
                    and key.endswith(port_postfix)
                    and key != watcher_key
                ):
                    stale_addrs.append(key.split(RAFT_PARTNER_PREFIX)[-1])
            return stale_addrs
        logger.warning("Collect stale addrs: No member available")
        return stale_addrs

    def remove_raft_member(
        self, member_address: str, raft_password: str, partner_addrs: list[str]
    ) -> None:
        """Remove a member from the raft cluster.

        The raft cluster is a different cluster from the Patroni cluster.
        It is responsible for defining which Patroni member can update
        the primary member in the DCS.

        Raises:
            RaftMemberNotFoundError: if the member to be removed
                is not part of the raft cluster.
        """
        if not member_address:
            logger.debug("Remove raft member: No address provided")
            return

        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=raft_password, timeout=3)

        for raft_host in [f"{addr}:{RAFT_PORT}" for addr in partner_addrs]:
            try:
                raft_status = syncobj_util.executeCommand(raft_host, ["status"])
            except Exception as e:
                logger.warning(f"Remove raft watcher: Cannot connect to raft cluster: {e}")
                continue
            if not raft_status:
                logger.warning("Remove raft watcher: No raft status")
                continue

            # Check whether the member is still part of the raft cluster.
            if f"{RAFT_PARTNER_PREFIX}{member_address}" not in raft_status:
                return

            # If there's no quorum and the leader left raft cluster is stuck
            if not raft_status["has_quorum"] or not raft_status["leader"]:
                logger.warning("Remove raft watcher: No quorum or leader")
                continue

            # Remove the member from the raft cluster.
            try:
                result = syncobj_util.executeCommand(raft_host, ["remove", member_address])
            except Exception as e:
                logger.debug(f"Remove raft watcher: Remove call failed {e}")
                continue

            if not result or not result.startswith("SUCCESS"):
                logger.debug(f"Remove raft watcher: Remove call not successful with {result}")
                continue
            return

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
            members: list[str] = [str(raft_status["self"])]
            for key in raft_status:
                if key.startswith(RAFT_PARTNER_PREFIX):
                    members.append(key[len(RAFT_PARTNER_PREFIX) :])
            status["members"] = sorted(members)
            return status
        except Exception as e:
            logger.debug(f"Error querying Raft status via TcpUtility: {e}")

        return status

    def check_all_endpoints(self, endpoints: list[str], password: str) -> dict[str, bool]:
        """Test connectivity to all PostgreSQL endpoints.

        WARNING: This method uses blocking time.sleep() for retry intervals
        (up to ~38s worst case with 2 endpoints). Only call from Juju actions,
        never from hook handlers.

        Args:
            endpoints: List of PostgreSQL unit IP addresses.
            password: Password for the watcher user.

        Returns:
            Dictionary mapping endpoint IP to health status data.
        """
        results: dict[str, bool] = {}
        for endpoint in endpoints:
            results[endpoint] = self._check_endpoint_with_retries(endpoint, password)

        self._last_health_results = results
        return results

    def _check_endpoint_with_retries(self, endpoint: str, password: str) -> bool:
        """Check a single endpoint with retry logic.

        Per acceptance criteria: Repeat tests at least 3 times before
        deciding that an instance is no longer reachable, waiting 7 seconds
        between every try.

        Args:
            endpoint: PostgreSQL endpoint IP address.
            password: Password for the watcher user.

        Returns:
            Dictionary with health status data.
        """
        with suppress(RetryError):
            for attempt in Retrying(
                stop=stop_after_attempt(DEFAULT_RETRY_COUNT),
                wait=wait_fixed(DEFAULT_RETRY_INTERVAL_SECONDS),
            ):
                with attempt:
                    if result := self._execute_health_query(endpoint, password):
                        logger.debug(f"Health check passed for {endpoint}")
                        return result
                    raise Exception(f"Cannot reach {endpoint}")

        logger.error(f"Endpoint {endpoint} unhealthy after {DEFAULT_RETRY_COUNT} attempts")
        return False

    def _execute_health_query(self, endpoint: str, password: str) -> bool:
        """Execute health check queries with TCP keepalive and timeout.

        Per acceptance criteria:
        - Testing actual queries (SELECT 1)
        - Using direct and reserved connections (no pgbouncer)
        - Setting TCP keepalive to avoid hanging on dead connections
        - Setting query timeout

        Args:
            endpoint: PostgreSQL endpoint IP address.
            password: Password for the watcher user.

        Returns:
            Dictionary with health info (is_in_recovery, etc.) or None if failed.
        """
        connection = None
        result = False
        try:
            # Connect directly to PostgreSQL port 5432 (not pgbouncer 6432)
            # Using the 'postgres' database which always exists
            with (
                psycopg2.connect(
                    host=endpoint,
                    port=5432,
                    dbname="postgres",
                    user="watcher",
                    password=password,
                    connect_timeout=DEFAULT_QUERY_TIMEOUT_SECONDS,
                    # TCP keepalive settings per acceptance criteria
                    keepalives=1,
                    keepalives_idle=TCP_KEEPALIVE_IDLE,
                    keepalives_interval=TCP_KEEPALIVE_INTERVAL,
                    keepalives_count=TCP_KEEPALIVE_COUNT,
                    # Set options for query timeout
                    options=f"-c statement_timeout={DEFAULT_QUERY_TIMEOUT_SECONDS * 1000}",
                ) as connection,
                connection.cursor() as cursor,
            ):
                # Query recovery status to determine primary vs replica
                cursor.execute("SELECT 1")
                result = True

        except psycopg2.Error as e:
            # Other database errors
            logger.debug(f"Database error on {endpoint}: {e}")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except psycopg2.Error as e:
                    logger.debug(f"Failed to close connection to {endpoint}: {e}")
        return result

    def cluster_status(self, endpoints: list[str]) -> list[ClusterMember]:
        """Query the cluster status."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        if response := parallel_patroni_get_request(
            f"/{PATRONI_CLUSTER_STATUS_ENDPOINT}", endpoints, self.ca_file, None
        ):
            logger.debug("API cluster_status: %s", response["members"])
            return response["members"]
        return []
