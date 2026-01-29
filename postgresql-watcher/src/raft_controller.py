# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Raft controller management for PostgreSQL watcher.

This module manages a native pysyncobj Raft node that participates in
consensus without running PostgreSQL, providing the necessary third vote
for quorum in 2-node PostgreSQL clusters.

The Raft service runs as a systemd service to ensure it persists between
charm hook invocations.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from pysyncobj.utility import TcpUtility, UtilityException
    PYSYNCOBJ_AVAILABLE = True
except ImportError:
    TcpUtility = None
    UtilityException = Exception
    PYSYNCOBJ_AVAILABLE = False

if TYPE_CHECKING:
    from charm import PostgreSQLWatcherCharm

logger = logging.getLogger(__name__)

# Raft configuration
RAFT_DATA_DIR = "/var/lib/watcher-raft"
RAFT_PORT = 2222

# Systemd service configuration
SERVICE_NAME = "watcher-raft"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"

# Path to the raft_service.py script in the charm
# During runtime, this will be in the charm's src directory
RAFT_SERVICE_SCRIPT = "/var/lib/juju/agents/unit-{unit_name}/charm/src/raft_service.py"

SERVICE_TEMPLATE = """[Unit]
Description=PostgreSQL Watcher Raft Service
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {script_path} --self-addr {self_addr} --partners {partners} --password {password} --data-dir {data_dir}
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

    def __init__(self, charm: "PostgreSQLWatcherCharm"):
        """Initialize the Raft controller.

        Args:
            charm: The PostgreSQL watcher charm instance.
        """
        self.charm = charm
        self._self_addr: str | None = None
        self._partner_addrs: list[str] = []
        self._password: str | None = None

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

        # Install/update systemd service
        self._install_service()

        logger.info(
            f"Raft controller configured: self={self_addr}, "
            f"partners={partner_addrs}"
        )

    def _get_script_path(self) -> str:
        """Get the path to the raft_service.py script."""
        # The script is in the charm's src directory
        unit_name = self.charm.unit.name.replace("/", "-")
        return RAFT_SERVICE_SCRIPT.format(unit_name=unit_name)

    def _install_service(self) -> None:
        """Install the systemd service for the Raft controller."""
        if not self._self_addr or not self._password:
            logger.warning("Cannot install service: not configured")
            return

        script_path = self._get_script_path()
        partners = ",".join(self._partner_addrs)

        service_content = SERVICE_TEMPLATE.format(
            script_path=script_path,
            self_addr=self._self_addr,
            partners=partners,
            password=self._password,
            data_dir=RAFT_DATA_DIR,
        )

        # Check if service file needs to be updated
        existing_content = ""
        if Path(SERVICE_FILE).exists():
            existing_content = Path(SERVICE_FILE).read_text()

        if existing_content == service_content:
            logger.debug("Systemd service already installed and up to date")
            return

        # Write service file
        Path(SERVICE_FILE).write_text(service_content)
        os.chmod(SERVICE_FILE, 0o644)

        # Reload systemd to pick up the new service
        try:
            subprocess.run(
                ["systemctl", "daemon-reload"],  # noqa: S603, S607
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Installed systemd service {SERVICE_NAME}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to reload systemd: {e.stderr}")
        except Exception as e:
            logger.error(f"Failed to reload systemd: {e}")

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
            subprocess.run(
                ["systemctl", "enable", SERVICE_NAME],  # noqa: S603, S607
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["systemctl", "start", SERVICE_NAME],  # noqa: S603, S607
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Started Raft controller service {SERVICE_NAME}")
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
            subprocess.run(
                ["systemctl", "stop", SERVICE_NAME],  # noqa: S603, S607
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Stopped Raft controller service {SERVICE_NAME}")
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
            subprocess.run(
                ["systemctl", "restart", SERVICE_NAME],  # noqa: S603, S607
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info(f"Restarted Raft controller service {SERVICE_NAME}")
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
            result = subprocess.run(
                ["systemctl", "is-active", SERVICE_NAME],  # noqa: S603, S607
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

    def _load_config_from_service(self) -> None:
        """Load configuration from the systemd service file if available.

        This is needed because each charm hook creates a fresh instance,
        and the configuration set via configure() is not persisted.
        """
        if self._self_addr and self._password:
            return  # Already configured

        if not Path(SERVICE_FILE).exists():
            return

        try:
            content = Path(SERVICE_FILE).read_text()
            # Parse ExecStart line to extract config
            for line in content.split("\n"):
                if line.startswith("ExecStart="):
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "--self-addr" and i + 1 < len(parts):
                            self._self_addr = parts[i + 1]
                        elif part == "--password" and i + 1 < len(parts):
                            self._password = parts[i + 1]
                        elif part == "--partners" and i + 1 < len(parts):
                            self._partner_addrs = parts[i + 1].split(",")
                    break
        except Exception as e:
            logger.debug(f"Failed to load config from service file: {e}")

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

        # Load config from service file if not already set
        self._load_config_from_service()

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
                    status["leader"] = str(raft_status.get("leader")) if raft_status.get("leader") else None
                    status["members"] = raft_status.get("members", [])
                    return status

            except UtilityException as e:
                logger.debug(f"Failed to query Raft status via TcpUtility: {e}")
            except Exception as e:
                logger.debug(f"Error querying Raft status via TcpUtility: {e}")

        # If TcpUtility failed or isn't available, but service is running,
        # assume we're connected (the service would fail if it couldn't bind)
        if is_running:
            status["connected"] = True
            logger.debug("Raft controller service is running, assuming connected")

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
