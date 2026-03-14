# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Raft controller management for PostgreSQL watcher.

This module manages a native pysyncobj Raft node that participates in
consensus without running PostgreSQL, providing the necessary third vote
for quorum in 2-node PostgreSQL clusters.

The Raft service runs as a systemd service to ensure it persists between
charm hook invocations.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from pysyncobj.utility import TcpUtility, UtilityException

    PYSYNCOBJ_AVAILABLE = True
except ImportError:
    TcpUtility = None  # type: ignore[assignment]
    UtilityException = Exception  # type: ignore[assignment]
    PYSYNCOBJ_AVAILABLE = False

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

# Base directory for all Raft instances
RAFT_BASE_DIR = "/var/lib/watcher-raft"

SERVICE_TEMPLATE = """[Unit]
Description=PostgreSQL Watcher Raft Service ({instance_id})
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {script_path} --self-addr {self_addr} --partners {partners} --password-file {password_file} --data-dir {data_dir}
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
        self.config_file = f"{RAFT_BASE_DIR}/{instance_id}/config.json"
        self.password_file = f"{RAFT_BASE_DIR}/{instance_id}/password"
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

        # Write password to a file with restricted permissions (not in service file or cmdline)
        self._write_password_file(password)

        # Write config to a JSON file for recovery across hook invocations
        self._write_config_file()

        # Install/update systemd service
        changed = self._install_service()

        logger.info(f"Raft controller configured: self={self_addr}, partners={partner_addrs}")
        return changed

    def _get_script_path(self) -> str:
        """Get the path to the raft_service.py script."""
        return str(Path(self.charm.charm_dir) / "src" / "raft_service.py")

    def _write_password_file(self, password: str) -> None:
        """Write the Raft password to a file with restricted permissions."""
        Path(self.password_file).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.password_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password)

    def _write_config_file(self) -> None:
        """Write Raft configuration to a JSON file for recovery across hooks."""
        config = {
            "self_addr": self._self_addr,
            "partner_addrs": self._partner_addrs,
        }
        fd = os.open(self.config_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(config))

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

        script_path = self._get_script_path()
        partners = ",".join(self._partner_addrs)

        service_content = SERVICE_TEMPLATE.format(
            instance_id=self.instance_id,
            script_path=script_path,
            self_addr=self._self_addr,
            partners=partners,
            password_file=self.password_file,
            data_dir=self.data_dir,
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
        except Exception as e:
            logger.error(f"Failed to reload systemd: {e}")

        return True

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
        """Load configuration from persistent files if available.

        This is needed because each charm hook creates a fresh instance,
        and the configuration set via configure() is not persisted in memory.
        """
        if self._self_addr and self._password:
            return  # Already configured

        # Load password from file
        password_path = Path(self.password_file)
        if password_path.exists():
            try:
                self._password = password_path.read_text().strip()
            except Exception as e:
                logger.debug(f"Failed to load password file: {e}")

        # Load config from JSON file
        config_path = Path(self.config_file)
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                self._self_addr = config.get("self_addr")
                self._partner_addrs = config.get("partner_addrs", [])
            except Exception as e:
                logger.debug(f"Failed to load config file: {e}")

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
                raft_status = utility.executeCommand(self._self_addr, ["status"])

                if raft_status:
                    status["connected"] = True
                    status["has_quorum"] = raft_status.get("has_quorum", False)
                    status["leader"] = (
                        str(raft_status.get("leader")) if raft_status.get("leader") else None
                    )
                    # Extract member addresses from partner_node_status_server_* keys
                    prefix = "partner_node_status_server_"
                    members: list[str] = [self._self_addr] if self._self_addr else []
                    for key in raft_status:
                        if isinstance(key, str) and key.startswith(prefix):
                            members.append(key[len(prefix) :])
                    status["members"] = sorted(members)
                    return status

            except UtilityException as e:
                logger.debug(f"Failed to query Raft status via TcpUtility: {e}")
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
