# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Raft controller management for PostgreSQL watcher.

This module provides a wrapper to manage the patroni_raft_controller process
from the charmed-postgresql snap. It is NOT a copy of Patroni's raft controller -
it simply configures and starts the existing patroni_raft_controller binary.

The patroni_raft_controller participates in Raft consensus without running
PostgreSQL, providing the necessary third vote for quorum in 2-node clusters.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading

try:
    from pysyncobj import FAIL_REASON, SyncObj, SyncObjConf
    from pysyncobj.utility import TcpUtility, UtilityException
    PYSYNCOBJ_AVAILABLE = True
except ImportError:
    SyncObj = None
    SyncObjConf = None
    FAIL_REASON = None
    TcpUtility = None
    UtilityException = Exception
    PYSYNCOBJ_AVAILABLE = False

if TYPE_CHECKING:
    from charm import PostgreSQLWatcherCharm

logger = logging.getLogger(__name__)

# Raft configuration paths
# Use snap's common data directory for config to ensure snap can access it
RAFT_DATA_DIR = "/var/snap/charmed-postgresql/common/watcher/raft"
RAFT_CONFIG_PATH = "/var/snap/charmed-postgresql/common/watcher/raft.yaml"
RAFT_PORT = 2222

# Patroni raft controller command (via snap run)
RAFT_CONTROLLER_CMD = ["snap", "run", "charmed-postgresql.patroni-raft-controller"]
# Legacy binary path (for backwards compatibility)
RAFT_CONTROLLER_BIN = "/snap/charmed-postgresql/current/usr/bin/patroni_raft_controller"


class WatcherRaftNode(SyncObj if SyncObj else object):
    """A minimal pysyncobj Raft node for the watcher.

    This node participates in Raft consensus without storing any
    application data - it only provides a vote for quorum.
    """

    def __init__(self, self_addr: str, partner_addrs: list[str], password: str):
        """Initialize the Raft node.

        Args:
            self_addr: This node's address (host:port).
            partner_addrs: List of partner addresses.
            password: Raft cluster password.
        """
        if not PYSYNCOBJ_AVAILABLE:
            return

        conf = SyncObjConf(
            password=password,
            autoTick=True,
            dynamicMembershipChange=True,
        )
        super().__init__(self_addr, partner_addrs, conf=conf)
        logger.info(f"WatcherRaftNode initialized: self={self_addr}, partners={partner_addrs}")


class RaftController:
    """Manages the Raft controller process for consensus participation."""

    def __init__(self, charm: "PostgreSQLWatcherCharm"):
        """Initialize the Raft controller.

        Args:
            charm: The PostgreSQL watcher charm instance.
        """
        self.charm = charm
        self._self_addr: str | None = None
        self._partner_addrs: list[str] = []
        self._password: str | None = None
        self._process: subprocess.Popen | None = None
        self._raft_node: WatcherRaftNode | None = None
        self._raft_thread: threading.Thread | None = None

    def configure(
        self,
        self_addr: str,
        partner_addrs: list[str],
        password: str,
    ) -> None:
        """Configure the Raft controller.

        Args:
            self_addr: This node's Raft address (ip:port).
            partner_addrs: List of partner Raft addresses.
            password: Raft cluster password.
        """
        self._self_addr = self_addr
        self._partner_addrs = partner_addrs
        self._password = password

        # Ensure data directory exists
        Path(RAFT_DATA_DIR).mkdir(parents=True, exist_ok=True)

        # Write configuration file
        self._write_config()

        logger.info(
            f"Raft controller configured: self={self_addr}, "
            f"partners={partner_addrs}"
        )

    def _write_config(self) -> None:
        """Write the Raft controller configuration file."""
        # Ensure config directory exists
        config_dir = Path(RAFT_CONFIG_PATH).parent
        config_dir.mkdir(parents=True, exist_ok=True)

        # Build configuration in the format expected by patroni_raft_controller
        # The config must be under a 'raft' key
        config_lines = [
            "raft:",
            f"  self_addr: '{self._self_addr}'",
            f"  data_dir: {RAFT_DATA_DIR}",
            f"  password: {self._password}",
        ]

        if self._partner_addrs:
            config_lines.append("  partner_addrs:")
            for addr in self._partner_addrs:
                config_lines.append(f"    - {addr}")

        config_content = "\n".join(config_lines)

        # Write config file with permissions that allow snap to read it
        # The snap runs in a confined environment and needs read access
        Path(RAFT_CONFIG_PATH).write_text(config_content)
        os.chmod(RAFT_CONFIG_PATH, 0o644)

        logger.debug(f"Wrote Raft config to {RAFT_CONFIG_PATH}")

    def start(self) -> bool:
        """Start the Raft controller process.

        Returns:
            True if started successfully, False otherwise.
        """
        if self.is_running():
            logger.debug("Raft controller already running")
            return True

        if not self._self_addr or not self._password:
            logger.error("Raft controller not configured")
            return False

        try:
            # Check if charmed-postgresql snap is installed
            try:
                subprocess.run(
                    ["snap", "list", "charmed-postgresql"],  # noqa: S607
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                snap_available = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                snap_available = False

            if not snap_available:
                logger.warning(
                    "charmed-postgresql snap not available, using embedded pysyncobj"
                )
                return self._start_embedded_raft()

            # Start the patroni_raft_controller via snap run
            self._process = subprocess.Popen(  # noqa: S603
                [*RAFT_CONTROLLER_CMD, RAFT_CONFIG_PATH],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            logger.info(f"Started Raft controller with PID {self._process.pid}")
            return True

        except Exception as e:
            logger.error(f"Failed to start Raft controller: {e}")
            return False

    def _start_embedded_raft(self) -> bool:
        """Start an embedded pysyncobj Raft node.

        This is a fallback when patroni_raft_controller is not available.

        Returns:
            True if started successfully, False otherwise.
        """
        if not PYSYNCOBJ_AVAILABLE:
            logger.error("pysyncobj not available, cannot start embedded Raft")
            return False

        try:
            self._raft_node = WatcherRaftNode(
                self._self_addr,
                self._partner_addrs,
                self._password,
            )
            logger.info(f"Started embedded pysyncobj Raft node at {self._self_addr}")
            return True
        except Exception as e:
            logger.error(f"Failed to start embedded Raft node: {e}")
            return False

    def stop(self) -> bool:
        """Stop the Raft controller process.

        Returns:
            True if stopped successfully, False otherwise.
        """
        # Stop embedded Raft node if running
        if self._raft_node is not None:
            try:
                self._raft_node.destroy()
                self._raft_node = None
                logger.info("Stopped embedded Raft node")
            except Exception as e:
                logger.error(f"Failed to stop embedded Raft node: {e}")
                return False

        if self._process is None:
            logger.debug("Raft controller not running")
            return True

        try:
            self._process.terminate()
            self._process.wait(timeout=10)
            self._process = None
            logger.info("Stopped Raft controller")
            return True
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process = None
            logger.warning("Killed Raft controller after timeout")
            return True
        except Exception as e:
            logger.error(f"Failed to stop Raft controller: {e}")
            return False

    def is_running(self) -> bool:
        """Check if the Raft controller is running.

        Returns:
            True if running, False otherwise.
        """
        # Check embedded Raft node
        if self._raft_node is not None:
            return True

        # Check if there's a patroni_raft_controller process running
        # This is needed because the _process variable doesn't persist across hook invocations
        try:
            result = subprocess.run(
                ["pgrep", "-f", "patroni_raft_controller"],  # noqa: S607
                capture_output=True,
                timeout=5,
            )
            logger.debug(f"pgrep result: returncode={result.returncode}, stdout={result.stdout}, stderr={result.stderr}")
            if result.returncode == 0:
                logger.debug("Found patroni_raft_controller process via pgrep")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"pgrep failed: {e}")

        if self._process is None:
            return False

        # Check if process is still alive
        return self._process.poll() is None

    def get_status(self) -> dict[str, Any]:
        """Get the Raft controller status.

        Returns:
            Dictionary with status information.
        """
        is_running = self.is_running()
        status: dict[str, Any] = {
            "running": is_running,
            "connected": False,
            "has_quorum": False,
            "leader": None,
            "members": [],
        }

        # If process is running, we can assume it's connected
        # (the process would exit if configuration was invalid)
        if is_running:
            status["connected"] = True
            logger.debug("Raft controller process is running, reporting connected")
            return status

        if not self._self_addr or not self._password:
            return status

        # If using embedded Raft node, query it directly
        if self._raft_node is not None:
            try:
                raft_status = self._raft_node.getStatus()
                status["connected"] = True
                status["has_quorum"] = raft_status.get("has_quorum", False)
                status["leader"] = str(raft_status.get("leader")) if raft_status.get("leader") else None
                status["members"] = [str(n) for n in (raft_status.get("nodes", []) or [])]
                return status
            except Exception as e:
                logger.debug(f"Failed to query embedded Raft status: {e}")
                # If we have a raft node but can't get status, still report connected
                status["connected"] = True
                return status

        # Query Raft status using pysyncobj TcpUtility
        if TcpUtility is not None:
            try:
                # Extract host:port from self_addr
                host, port = self._self_addr.rsplit(":", 1)
                raft_host = f"{host}:{port}"

                utility = TcpUtility(password=self._password, timeout=3)
                raft_status = utility.executeCommand(raft_host, ["status"])

                if raft_status:
                    status["connected"] = True
                    status["has_quorum"] = raft_status.get("has_quorum", False)
                    status["leader"] = raft_status.get("leader")
                    status["members"] = raft_status.get("members", [])
                    return status

            except UtilityException as e:
                logger.debug(f"Failed to query Raft status via TcpUtility: {e}")
            except Exception as e:
                logger.debug(f"Error querying Raft status via TcpUtility: {e}")

        # If TcpUtility failed or isn't available, but process is running,
        # assume we're connected (the process would exit if it couldn't connect)
        if is_running:
            status["connected"] = True
            logger.debug("Raft controller process is running, assuming connected")

        return status

    def has_quorum(self) -> bool:
        """Check if the Raft cluster has quorum.

        Returns:
            True if quorum is established, False otherwise.
        """
        status = self.get_status()
        return status.get("has_quorum", False)

    def get_leader(self) -> str | None:
        """Get the current Raft leader.

        Returns:
            Leader address, or None if no leader.
        """
        status = self.get_status()
        return status.get("leader")

    def get_members(self) -> list[str]:
        """Get the list of Raft cluster members.

        Returns:
            List of member addresses.
        """
        status = self.get_status()
        return status.get("members", [])
