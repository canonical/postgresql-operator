#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Watcher Charm.

A lightweight witness/voter charm for PostgreSQL stereo mode (2-node clusters).
Participates in Raft consensus to provide quorum without running PostgreSQL.
"""

import json
import logging
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

        # Install charmed-postgresql snap to get patroni_raft_controller
        try:
            self.unit.status = MaintenanceStatus("Installing charmed-postgresql snap")
            subprocess.run(
                ["snap", "install", "charmed-postgresql", "--channel=16/edge"],  # noqa: S607
                check=True,
                capture_output=True,
                timeout=300,
            )
            logger.info("charmed-postgresql snap installed successfully")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to install charmed-postgresql snap: {e.stderr}")
        except subprocess.TimeoutExpired:
            logger.warning("Timeout installing charmed-postgresql snap")
        except FileNotFoundError:
            logger.warning("snap command not found")

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

        # Run health checks (optional - doesn't block on failures)
        pg_endpoints = self._get_pg_endpoints()
        if not pg_endpoints:
            # Still active if Raft is connected but endpoints aren't available yet
            self.unit.status = ActiveStatus("Raft connected, waiting for PostgreSQL endpoints")
            return

        # Perform health check (non-blocking - just for monitoring)
        try:
            health_results = self.health_checker.check_all_endpoints(pg_endpoints)
            healthy_count = sum(1 for healthy in health_results.values() if healthy)

            if healthy_count == len(pg_endpoints):
                self.unit.status = ActiveStatus(
                    f"Monitoring {len(pg_endpoints)} PostgreSQL endpoints"
                )
            elif healthy_count > 0:
                self.unit.status = ActiveStatus(
                    f"Monitoring {healthy_count}/{len(pg_endpoints)} healthy endpoints"
                )
            else:
                # Even if health checks fail, remain active since Raft is working
                # Health check failures are logged but don't block the watcher
                self.unit.status = ActiveStatus(
                    f"Raft connected, health checks failing for {len(pg_endpoints)} endpoints"
                )
        except Exception as e:
            logger.warning(f"Health check exception: {e}")
            self.unit.status = ActiveStatus("Raft connected")

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

        # Configure and start Raft controller
        self.raft_controller.configure(
            self_addr=f"{self.unit_ip}:{RAFT_PORT}",
            partner_addrs=[f"{addr}:{RAFT_PORT}" for addr in partner_addrs],
            password=raft_password,
        )

        if not self.raft_controller.is_running():
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
            "healthy_count": sum(1 for h in health_results.values() if h),
            "total_count": len(health_results),
        }

        event.set_results(results)


if __name__ == "__main__":
    ops.main(PostgreSQLWatcherCharm)
