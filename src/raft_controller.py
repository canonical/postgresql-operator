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

import importlib
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

TcpUtility: type[Any] | None = None
UtilityException: type[Exception] = Exception
try:
    utility_module = importlib.import_module("pysyncobj.utility")
    TcpUtility = utility_module.TcpUtility
    UtilityException = utility_module.UtilityException
    PYSYNCOBJ_AVAILABLE = True
except ImportError:
    PYSYNCOBJ_AVAILABLE = False

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

# Base directory for all Raft instances.
# Must be under the snap's common path so that
# charmed-postgresql.patroni-raft-controller can access it.
RAFT_BASE_DIR = "/var/snap/charmed-postgresql/common/watcher-raft"

SERVICE_TEMPLATE = """[Unit]
Description=PostgreSQL Watcher Raft Service ({instance_id})
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/bin/snap run charmed-postgresql.patroni-raft-controller {config_file}
Restart=always
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


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
        self._self_addr: str | None = None
        self._partner_addrs: list[str] = []
        self._password: str | None = None

        # Derive all paths from instance_id
        self.data_dir = f"{RAFT_BASE_DIR}/{instance_id}"
        self.config_file = f"{RAFT_BASE_DIR}/{instance_id}/patroni-raft.yaml"
        self.service_name = f"watcher-raft-{instance_id}"
        self.service_file = f"/etc/systemd/system/watcher-raft-{instance_id}.service"

    def configure(
        self,
        self_addr: str,
        partner_addrs: list[str],
        password: str,
    ) -> bool:
        """Configure the Raft controller.

        Args:
            self_addr: This node's Raft address (ip:port).
            partner_addrs: List of partner Raft addresses.
            password: Raft cluster password.

        Returns:
            True if configuration changed, False if unchanged.
        """
        self._self_addr = self_addr
        self._partner_addrs = partner_addrs
        self._password = password

        # Ensure data directory exists
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)

        # Write Patroni-compatible YAML config (includes password)
        config_changed = self._write_config_file()

        # Install/update systemd service
        service_changed = self._install_service()

        logger.info(f"Raft controller configured: self={self_addr}, partners={partner_addrs}")
        return config_changed or service_changed

    def _write_config_file(self) -> bool:
        """Write Raft configuration as a Patroni-compatible YAML file.

        The patroni_raft_controller expects a YAML config with a ``raft:``
        section containing self_addr, partner_addrs, password, and data_dir.

        Returns:
            True if the config file changed, False if unchanged.
        """
        # Build YAML manually to avoid adding pyyaml as a dependency.
        # The values are validated addresses and a password string, so
        # simple formatting is safe.
        partner_lines = ""
        for addr in self._partner_addrs:
            partner_lines += f"\n    - '{addr}'"

        yaml_content = f"""raft:
  self_addr: '{self._self_addr}'
  partner_addrs:{partner_lines}
  password: '{self._password}'
  data_dir: '{self.data_dir}/raft'
"""
        config_path = Path(self.config_file)
        if config_path.exists():
            try:
                if config_path.read_text() == yaml_content:
                    logger.debug("Raft config already up to date")
                    return False
            except OSError as e:
                logger.warning(f"Failed reading existing Raft config: {e}")

        Path(f"{self.data_dir}/raft").mkdir(parents=True, exist_ok=True)
        fd = os.open(self.config_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(yaml_content)
        return True

    def _install_service(self) -> bool:
        """Install the systemd service for the Raft controller.

        Returns:
            True if the service file was updated, False if unchanged.
        """
        if not self._self_addr or not self._password:
            logger.warning("Cannot install service: not configured")
            return False

        # Validate addresses to prevent injection into the systemd unit file
        addr_pattern = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5}$")
        if not addr_pattern.match(self._self_addr):
            logger.error(f"Invalid self_addr format: {self._self_addr}")
            return False
        for addr in self._partner_addrs:
            if not addr_pattern.match(addr):
                logger.error(f"Invalid partner address format: {addr}")
                return False

        service_content = SERVICE_TEMPLATE.format(
            instance_id=self.instance_id,
            config_file=self.config_file,
        )

        # Check if service file needs to be updated
        existing_content = ""
        if Path(self.service_file).exists():
            existing_content = Path(self.service_file).read_text()

        if existing_content == service_content:
            logger.debug("Systemd service already installed and up to date")
            return False

        # Write service file
        Path(self.service_file).write_text(service_content)
        os.chmod(self.service_file, 0o644)

        success = True

        # Reload systemd to pick up the new service
        try:
            subprocess.run(
                ["/usr/bin/systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Installed systemd service {self.service_name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to reload systemd: {e.stderr}")
            success = False
        except Exception as e:
            logger.error(f"Failed to reload systemd: {e}")
            success = False

        return success

    def start(self) -> bool:
        """Start the Raft controller service.

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
            # Enable and start the service
            subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", "enable", self.service_name],
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", "start", self.service_name],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Started Raft controller service {self.service_name}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start Raft controller: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Failed to start Raft controller: {e}")
            return False

    def stop(self) -> bool:
        """Stop the Raft controller service.

        Returns:
            True if stopped successfully, False otherwise.
        """
        if not self.is_running():
            logger.debug("Raft controller not running")
            return True

        try:
            subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", "stop", self.service_name],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Stopped Raft controller service {self.service_name}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to stop Raft controller: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Failed to stop Raft controller: {e}")
            return False

    def remove_service(self) -> bool:
        """Disable and remove the Raft systemd service unit file."""
        success = True

        if self.is_running() and not self.stop():
            success = False

        try:
            enabled_result = subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", "is-enabled", self.service_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as e:
            logger.error(f"Timed out checking if service is enabled: {e}")
            return False

        if enabled_result.returncode == 0:
            try:
                subprocess.run(  # noqa: S603
                    ["/usr/bin/systemctl", "disable", self.service_name],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to disable Raft controller service: {e.stderr}")
                success = False
            except subprocess.TimeoutExpired as e:
                logger.error(f"Timed out disabling Raft controller service: {e}")
                success = False

        service_path = Path(self.service_file)
        if service_path.exists():
            try:
                service_path.unlink()
            except OSError as e:
                logger.error(f"Failed to remove service file {self.service_file}: {e}")
                success = False

        try:
            subprocess.run(
                ["/usr/bin/systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to reload systemd after service removal: {e.stderr}")
            success = False
        except subprocess.TimeoutExpired as e:
            logger.error(f"Timed out reloading systemd after service removal: {e}")
            success = False

        return success

    def restart(self) -> bool:
        """Restart the Raft controller service.

        Returns:
            True if restarted successfully, False otherwise.
        """
        try:
            subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", "restart", self.service_name],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Restarted Raft controller service {self.service_name}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart Raft controller: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Failed to restart Raft controller: {e}")
            return False

    def is_running(self) -> bool:
        """Check if the Raft controller service is running.

        Returns:
            True if running, False otherwise.
        """
        try:
            result = subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", "is-active", self.service_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            is_active = result.stdout.strip() == "active"
            if is_active:
                logger.debug("Raft controller service is active")
            return is_active
        except Exception as e:
            logger.debug(f"Failed to check service status: {e}")
            return False

    def _load_config(self) -> None:
        """Load configuration from the YAML config file if available.

        This is needed because each charm hook creates a fresh instance,
        and the configuration set via configure() is not persisted in memory.
        """
        if self._self_addr and self._password:
            return  # Already configured

        config_path = Path(self.config_file)
        if not config_path.exists():
            return

        try:
            # Parse the YAML config manually (simple key: value format)
            content = config_path.read_text()
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("self_addr:"):
                    self._self_addr = line.split(":", 1)[1].strip().strip("'\"")
                elif line.startswith("password:"):
                    self._password = line.split(":", 1)[1].strip().strip("'\"")
                elif line.startswith("- '") and line.endswith("'"):
                    self._partner_addrs.append(line.strip("- '\""))
        except Exception as e:
            logger.debug(f"Failed to load config file: {e}")

    def _status_query_targets(self) -> list[str]:
        """Build Raft status probe targets for this local unit.

        Returns:
            Ordered list of addresses to query with TcpUtility.
        """
        if not self._self_addr:
            return []

        targets = [self._self_addr]

        # In some environments the controller advertises a routable unit IP
        # but local administration works only through loopback on the same port.
        host_port = self._self_addr.rsplit(":", maxsplit=1)
        if len(host_port) == 2 and host_port[1].isdigit():
            localhost_addr = f"127.0.0.1:{host_port[1]}"
            if localhost_addr not in targets:
                targets.append(localhost_addr)

        return targets

    def _query_raft_status(self, utility: Any, target: str) -> dict[str, Any] | None:
        """Query Raft status for a specific target address."""
        try:
            raft_status = utility.executeCommand(target, ["status"])
        except UtilityException as e:
            logger.debug(f"Failed to query Raft status via TcpUtility (target={target}): {e}")
            return None
        except Exception as e:
            logger.debug(f"Error querying Raft status via TcpUtility (target={target}): {e}")
            return None
        return raft_status if isinstance(raft_status, dict) else None

    def _populate_status(
        self, status: dict[str, Any], raft_status: dict[str, Any]
    ) -> dict[str, Any]:
        """Populate public status fields from a Raft status payload."""
        status["connected"] = True
        status["has_quorum"] = raft_status.get("has_quorum", False)
        status["leader"] = str(raft_status.get("leader")) if raft_status.get("leader") else None

        # Extract member addresses from partner_node_status_server_* keys
        prefix = "partner_node_status_server_"
        members: list[str] = [self._self_addr] if self._self_addr else []
        for key in raft_status:
            if isinstance(key, str) and key.startswith(prefix):
                members.append(key[len(prefix) :])
        status["members"] = sorted(members)
        return status

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

        # Load config from persistent files if not already set
        self._load_config()

        if not self._self_addr or not self._password:
            return status

        # Query Raft status using pysyncobj TcpUtility
        if TcpUtility is not None and is_running:
            try:
                utility = TcpUtility(password=self._password, timeout=3)
                for target in self._status_query_targets():
                    raft_status = self._query_raft_status(utility, target)
                    if raft_status:
                        return self._populate_status(status, raft_status)
            except Exception as e:
                logger.debug(f"Error querying Raft status via TcpUtility: {e}")

        # If TcpUtility isn't available (pysyncobj not installed in charm venv)
        # but the service is running, assume connected as a fallback.
        # If TcpUtility IS available but the query failed, leave connected=False
        # since the node may not be ready yet.
        if is_running and not PYSYNCOBJ_AVAILABLE:
            status["connected"] = True
            logger.debug("Raft controller service is running (TcpUtility not available)")

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
