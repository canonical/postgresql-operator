#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Watcher Charm.

A lightweight witness/voter charm for PostgreSQL stereo mode (2-node clusters).
Participates in Raft consensus to provide quorum without running PostgreSQL.
"""

import json
import logging
import os
import subprocess
from typing import Any

import ops
from ops import (
    ActionEvent,
    ActiveStatus,
    ConfigChangedEvent,
    InstallEvent,
    MaintenanceStatus,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
    SecretNotFoundError,
    StartEvent,
    UpdateStatusEvent,
    WaitingStatus,
)
from raft_controller import RaftController
from watcher import HealthChecker

logger = logging.getLogger(__name__)

WATCHER_RELATION = "watcher"
RAFT_PORT = 2222


class PostgreSQLWatcherCharm(ops.CharmBase):
    """Charm for PostgreSQL Watcher/Witness node."""

    def __init__(self, *args):
        super().__init__(*args)

        self.health_checker = HealthChecker(self)
        self.raft_controller = RaftController(self)

        # Lifecycle events
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)

        # Relation events
        self.framework.observe(
            self.on[WATCHER_RELATION].relation_joined,
            self._on_watcher_relation_joined,
        )
        self.framework.observe(
            self.on[WATCHER_RELATION].relation_changed,
            self._on_watcher_relation_changed,
        )
        self.framework.observe(
            self.on[WATCHER_RELATION].relation_departed,
            self._on_watcher_relation_departed,
        )
        self.framework.observe(
            self.on[WATCHER_RELATION].relation_broken,
            self._on_watcher_relation_broken,
        )

        # Actions
        self.framework.observe(self.on.show_topology_action, self._on_show_topology)
        self.framework.observe(
            self.on.trigger_health_check_action, self._on_trigger_health_check
        )

    @property
    def _relation(self) -> ops.Relation | None:
        """Return the watcher relation if it exists."""
        return self.model.get_relation(WATCHER_RELATION)

    @property
    def unit_ip(self) -> str:
        """Return this unit's IP address."""
        return str(self.model.get_binding(WATCHER_RELATION).network.bind_address)

    @property
    def is_related(self) -> bool:
        """Check if the watcher is related to a PostgreSQL cluster."""
        return self._relation is not None and len(self._relation.units) > 0

    def _get_raft_password(self) -> str | None:
        """Get the Raft password from the relation secret.

        Returns:
            The Raft password, or None if not available.
        """
        if not (relation := self._relation):
            return None

        secret_id = relation.data[relation.app].get("raft-secret-id")
        if not secret_id:
            return None

        try:
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content(refresh=True)
            return content.get("raft-password")
        except SecretNotFoundError:
            logger.warning(f"Secret {secret_id} not found")
            return None

    def _get_pg_endpoints(self) -> list[str]:
        """Get PostgreSQL endpoints from the relation.

        Returns:
            List of PostgreSQL unit IP addresses.
        """
        if not (relation := self._relation):
            return []

        pg_endpoints_json = relation.data[relation.app].get("pg-endpoints")
        if not pg_endpoints_json:
            return []

        try:
            return json.loads(pg_endpoints_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse pg-endpoints JSON")
            return []

    def _get_raft_partner_addrs(self) -> list[str]:
        """Get Raft partner addresses from the relation.

        Returns:
            List of Raft partner addresses (PostgreSQL units).
        """
        if not (relation := self._relation):
            return []

        raft_addrs_json = relation.data[relation.app].get("raft-partner-addrs")
        if not raft_addrs_json:
            return []

        try:
            return json.loads(raft_addrs_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse raft-partner-addrs JSON")
            return []

    def _on_install(self, event: InstallEvent) -> None:
        """Handle install event."""
        self.unit.status = MaintenanceStatus("Installing watcher components")

        # Install pysyncobj system-wide for the Raft service
        # The Raft service runs as a systemd service with system Python,
        # so we need pysyncobj installed system-wide.
        # Use --break-system-packages for Ubuntu 24.04+ (PEP 668)
        # IMPORTANT: Use /usr/bin/python3 -m pip to ensure we use system Python's pip,
        # not any venv pip that the charm framework might inject via PATH.
        try:
            self.unit.status = MaintenanceStatus("Installing pysyncobj")
            # First ensure pip is installed
            subprocess.run(
                ["apt-get", "update"],  # noqa: S607
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "python3-pip"],  # noqa: S607
                check=True,
                capture_output=True,
                timeout=300,
            )
            # Use /usr/bin/python3 -m pip to install to system Python
            # Clear PYTHONPATH to ensure pip installs to system site-packages
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                ["/usr/bin/python3", "-m", "pip", "install", "--break-system-packages", "pysyncobj"],  # noqa: S607
                check=True,
                capture_output=True,
                timeout=120,
                env=env,
            )
            logger.info(f"pysyncobj installed successfully: {result.stdout.decode()}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install pysyncobj: {e.stderr}")
            # This is critical - defer the event to retry
            event.defer()
            return
        except subprocess.TimeoutExpired:
            logger.error("Timeout installing pysyncobj")
            event.defer()
            return
        except FileNotFoundError:
            logger.error("pip3 command not found")
            event.defer()
            return

        logger.info("PostgreSQL Watcher charm installed")

    def _on_start(self, event: StartEvent) -> None:
        """Handle start event."""
        if not self.is_related:
            self.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return

        self.unit.status = ActiveStatus()

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Handle config changed event."""
        self.health_checker.update_config(
            interval=self.config["health-check-interval"],
            timeout=self.config["health-check-timeout"],
            retries=self.config["health-check-retries"],
            retry_interval=self.config["retry-interval"],
        )

    def _on_update_status(self, event: UpdateStatusEvent) -> None:
        """Handle update status event."""
        if not self.is_related:
            self.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return

        # Check Raft controller status
        raft_status = self.raft_controller.get_status()
        if not raft_status.get("connected"):
            self.unit.status = WaitingStatus("Connecting to Raft cluster")
            return

        # Get PostgreSQL endpoints count for status message
        pg_endpoints = self._get_pg_endpoints()
        endpoint_count = len(pg_endpoints)

        # Note: Health checks are only run on-demand via the trigger-health-check action
        # because the watcher doesn't have PostgreSQL credentials. The Raft consensus
        # is what matters for stereo mode - Patroni handles actual failover decisions.
        if endpoint_count > 0:
            self.unit.status = ActiveStatus(
                f"Raft connected, monitoring {endpoint_count} PostgreSQL endpoints"
            )
        else:
            self.unit.status = ActiveStatus("Raft connected, waiting for PostgreSQL endpoints")

    def _on_watcher_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle watcher relation joined event."""
        logger.info("Joined watcher relation with PostgreSQL cluster")

        # Share our unit address
        event.relation.data[self.unit]["unit-address"] = self.unit_ip

    def _on_watcher_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle watcher relation changed event."""
        logger.info("Watcher relation data changed")

        # Get Raft password and partner addresses
        raft_password = self._get_raft_password()
        if not raft_password:
            logger.debug("Raft password not yet available")
            event.defer()
            return

        partner_addrs = self._get_raft_partner_addrs()
        if not partner_addrs:
            logger.debug("Raft partner addresses not yet available")
            event.defer()
            return

        # Configure and start Raft controller (as a systemd service)
        # The configure() method writes config and installs the service
        self.raft_controller.configure(
            self_addr=f"{self.unit_ip}:{RAFT_PORT}",
            partner_addrs=[f"{addr}:{RAFT_PORT}" for addr in partner_addrs],
            password=raft_password,
        )

        # Start the service if not running, or restart if config changed
        if self.raft_controller.is_running():
            # Restart to pick up any config changes
            logger.info("Restarting Raft controller to apply config changes")
            self.raft_controller.restart()
        else:
            logger.info("Starting Raft controller service")
            self.raft_controller.start()

        # Update unit data
        event.relation.data[self.unit]["unit-address"] = self.unit_ip
        event.relation.data[self.unit]["raft-status"] = "connected"

        self.unit.status = ActiveStatus()

    def _on_watcher_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle watcher relation departed event."""
        logger.info("PostgreSQL unit departed from watcher relation")

    def _on_watcher_relation_broken(self, event) -> None:
        """Handle watcher relation broken event."""
        logger.info("Watcher relation broken")

        # Stop Raft controller
        self.raft_controller.stop()

        self.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")

    def _on_show_topology(self, event: ActionEvent) -> None:
        """Handle show-topology action."""
        topology: dict[str, Any] = {
            "watcher": {
                "unit": self.unit.name,
                "ip": self.unit_ip,
            },
            "postgresql_endpoints": [],
            "raft_status": {},
        }

        # Get PostgreSQL endpoints
        pg_endpoints = self._get_pg_endpoints()
        for endpoint in pg_endpoints:
            topology["postgresql_endpoints"].append({
                "ip": endpoint,
            })

        # Get Raft status
        topology["raft_status"] = self.raft_controller.get_status()

        # Get health check results
        if pg_endpoints:
            health_results = self.health_checker.check_all_endpoints(pg_endpoints)
            for i, endpoint in enumerate(pg_endpoints):
                if i < len(topology["postgresql_endpoints"]):
                    topology["postgresql_endpoints"][i]["healthy"] = health_results.get(
                        endpoint, False
                    )

        event.set_results({"topology": json.dumps(topology, indent=2)})

    def _on_trigger_health_check(self, event: ActionEvent) -> None:
        """Handle trigger-health-check action."""
        pg_endpoints = self._get_pg_endpoints()

        if not pg_endpoints:
            event.fail("No PostgreSQL endpoints available")
            return

        health_results = self.health_checker.check_all_endpoints(pg_endpoints)

        results = {
            "endpoints": json.dumps(
                {endpoint: "healthy" if healthy else "unhealthy"
                 for endpoint, healthy in health_results.items()},
                indent=2
            ),
            "healthy-count": sum(1 for h in health_results.values() if h),
            "total-count": len(health_results),
        }

        event.set_results(results)


if __name__ == "__main__":
    ops.main(PostgreSQLWatcherCharm)
