# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Watcher Requirer Relation implementation.

This module handles the watcher (requirer) side of the relation, used when the
charm is deployed with role=watcher. It connects to a PostgreSQL application
(which provides the watcher-offer relation) and participates in Raft consensus
as a lightweight witness for stereo mode (2-node clusters).
"""

import json
import logging
import os
import subprocess
import typing

from ops import (
    ActionEvent,
    ActiveStatus,
    BlockedStatus,
    ConfigChangedEvent,
    InstallEvent,
    MaintenanceStatus,
    Object,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
    SecretNotFoundError,
    StartEvent,
    UpdateStatusEvent,
    WaitingStatus,
)

from constants import (
    RAFT_PORT,
    WATCHER_RELATION,
)

if typing.TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)


class WatcherRequirerHandler(Object):
    """Handles the watcher requirer relation and watcher-mode lifecycle."""

    def __init__(self, charm: "PostgresqlOperatorCharm"):
        super().__init__(charm, WATCHER_RELATION)
        self.charm = charm

        # Lazy imports to avoid importing when not in watcher mode
        from raft_controller import RaftController
        from watcher_health import HealthChecker

        self.health_checker = HealthChecker(charm, password_getter=self.get_watcher_password)
        self.raft_controller = RaftController(charm)

        # Lifecycle events
        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)
        self.framework.observe(self.charm.on.update_status, self._on_update_status)

        # Relation events
        self.framework.observe(
            self.charm.on[WATCHER_RELATION].relation_joined,
            self._on_watcher_relation_joined,
        )
        self.framework.observe(
            self.charm.on[WATCHER_RELATION].relation_changed,
            self._on_watcher_relation_changed,
        )
        self.framework.observe(
            self.charm.on[WATCHER_RELATION].relation_departed,
            self._on_watcher_relation_departed,
        )
        self.framework.observe(
            self.charm.on[WATCHER_RELATION].relation_broken,
            self._on_watcher_relation_broken,
        )

        # Actions
        self.framework.observe(self.charm.on.show_topology_action, self._on_show_topology)
        self.framework.observe(
            self.charm.on.trigger_health_check_action, self._on_trigger_health_check
        )

    @property
    def _relation(self):
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
        """Get the Raft password from the relation secret."""
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

    def get_watcher_password(self) -> str | None:
        """Get the watcher PostgreSQL user password from the relation secret."""
        if not (relation := self._relation):
            return None

        secret_id = relation.data[relation.app].get("raft-secret-id")
        if not secret_id:
            return None

        try:
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content(refresh=True)
            return content.get("watcher-password")
        except SecretNotFoundError:
            logger.warning(f"Secret {secret_id} not found")
            return None

    def _get_pg_endpoints(self) -> list[str]:
        """Get PostgreSQL endpoints from the relation."""
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
        """Get Raft partner addresses from the relation."""
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

    # -- Lifecycle events --

    def _on_install(self, event: InstallEvent) -> None:
        """Install watcher components (skip PostgreSQL snap)."""
        self.charm.unit.status = MaintenanceStatus("Installing watcher components")

        try:
            self.charm.unit.status = MaintenanceStatus("Installing pysyncobj")
            subprocess.run(
                ["/usr/bin/apt-get", "update"],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["/usr/bin/apt-get", "install", "-y", "python3-pip"],
                check=True,
                capture_output=True,
                timeout=300,
            )
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-m",
                    "pip",
                    "install",
                    "--break-system-packages",
                    "pysyncobj",
                ],
                check=True,
                capture_output=True,
                timeout=120,
                env=env,
            )
            logger.info(f"pysyncobj installed successfully: {result.stdout.decode()}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install pysyncobj: {e.stderr}")
            event.defer()
            return
        except subprocess.TimeoutExpired:
            logger.error("Timeout installing pysyncobj")
            event.defer()
            return

        self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
        logger.info("Watcher mode install complete")

    def _on_start(self, event: StartEvent) -> None:
        """Handle start event in watcher mode."""
        if not self.is_related:
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return
        self.charm.unit.status = ActiveStatus()

    def _update_unit_address_if_changed(self) -> None:
        """Update unit-address in relation data if IP has changed."""
        if not (relation := self._relation):
            return

        current_address = relation.data[self.charm.unit].get("unit-address")
        new_address = self.unit_ip
        if current_address == new_address:
            return

        logger.info(
            f"Unit IP changed from {current_address} to {new_address}, updating relation data"
        )
        relation.data[self.charm.unit]["unit-address"] = new_address

        raft_password = self._get_raft_password()
        partner_addrs = self._get_raft_partner_addrs()
        if raft_password and partner_addrs:
            self.raft_controller.configure(
                self_addr=f"{new_address}:{RAFT_PORT}",
                partner_addrs=[f"{addr}:{RAFT_PORT}" for addr in partner_addrs],
                password=raft_password,
            )
            if self.raft_controller.is_running():
                logger.info("Restarting Raft controller due to IP change")
                self.raft_controller.restart()

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Handle config changed event in watcher mode."""
        self._update_unit_address_if_changed()

    def _on_update_status(self, event: UpdateStatusEvent) -> None:
        """Handle update status event in watcher mode."""
        if not self.is_related:
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return

        self._update_unit_address_if_changed()

        raft_status = self.raft_controller.get_status()
        if not raft_status.get("connected"):
            self.charm.unit.status = WaitingStatus("Connecting to Raft cluster")
            return

        pg_endpoints = self._get_pg_endpoints()
        endpoint_count = len(pg_endpoints)

        if endpoint_count > 0:
            self.charm.unit.status = ActiveStatus(
                f"Raft connected, monitoring {endpoint_count} PostgreSQL endpoints"
            )
        else:
            self.charm.unit.status = ActiveStatus(
                "Raft connected, waiting for PostgreSQL endpoints"
            )

    # -- Relation events --

    def _on_watcher_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle watcher relation joined event."""
        logger.info("Joined watcher relation with PostgreSQL cluster")
        event.relation.data[self.charm.unit]["unit-address"] = self.unit_ip

    def _on_watcher_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle watcher relation changed event."""
        logger.info("Watcher relation data changed")

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

        self.raft_controller.configure(
            self_addr=f"{self.unit_ip}:{RAFT_PORT}",
            partner_addrs=[f"{addr}:{RAFT_PORT}" for addr in partner_addrs],
            password=raft_password,
        )

        if self.raft_controller.is_running():
            logger.info("Restarting Raft controller to apply config changes")
            self.raft_controller.restart()
        else:
            logger.info("Starting Raft controller service")
            self.raft_controller.start()

        event.relation.data[self.charm.unit]["unit-address"] = self.unit_ip
        event.relation.data[self.charm.unit]["raft-status"] = "connected"

        self.charm.unit.status = ActiveStatus()

    def _on_watcher_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle watcher relation departed event."""
        logger.info("PostgreSQL unit departed from watcher relation")

    def _on_watcher_relation_broken(self, event) -> None:
        """Handle watcher relation broken event."""
        logger.info("Watcher relation broken")
        self.raft_controller.stop()
        self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")

    # -- Actions --

    def _on_show_topology(self, event: ActionEvent) -> None:
        """Handle show-topology action."""
        topology = {
            "watcher": {
                "unit": self.charm.unit.name,
                "ip": self.unit_ip,
            },
            "postgresql_endpoints": [],
            "raft_status": {},
        }

        pg_endpoints = self._get_pg_endpoints()
        for endpoint in pg_endpoints:
            topology["postgresql_endpoints"].append({"ip": endpoint})

        topology["raft_status"] = self.raft_controller.get_status()

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
                {
                    endpoint: "healthy" if healthy else "unhealthy"
                    for endpoint, healthy in health_results.items()
                },
                indent=2,
            ),
            "healthy-count": sum(1 for h in health_results.values() if h),
            "total-count": len(health_results),
        }

        event.set_results(results)
