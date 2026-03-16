# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Watcher Requirer Relation implementation.

This module handles the watcher (requirer) side of the relation, used when the
charm is deployed with role=watcher. It connects to one or more PostgreSQL
applications (which provide the watcher-offer relation) and participates in
Raft consensus as a lightweight witness for stereo mode (2-node clusters).

Multi-cluster support:
- Each watcher relation gets its own RaftController instance
- Ports are assigned dynamically starting from RAFT_PORT (2222) and persisted
  in a port allocation file at /var/snap/charmed-postgresql/common/watcher-raft/ports.json
- Each RaftController uses instance-specific data directories and systemd services
"""

from __future__ import annotations

import json
import logging
import os
import typing
from pathlib import Path
from typing import Any

from ops import (
    ActionEvent,
    ActiveStatus,
    BlockedStatus,
    InstallEvent,
    MaintenanceStatus,
    Object,
    Relation,
    RelationBrokenEvent,
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
    from raft_controller import RaftController

logger = logging.getLogger(__name__)

SNAP_NAME = "charmed-postgresql"
SNAP_CHANNEL = "16/edge/neppel"

# Port allocation file for persistent port mapping across hooks
PORTS_FILE = "/var/snap/charmed-postgresql/common/watcher-raft/ports.json"


class WatcherRequirerHandler(Object):
    """Handles the watcher requirer relation and watcher-mode lifecycle."""

    def __init__(self, charm: PostgresqlOperatorCharm):
        super().__init__(charm, WATCHER_RELATION)
        self.charm = charm

        # Per-relation RaftControllers, keyed by relation ID
        self._raft_controllers: dict[int, RaftController] = {}

        # Lifecycle events
        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
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
    def unit_ip(self) -> str | None:
        """Return this unit's IP address."""
        if binding := self.model.get_binding(WATCHER_RELATION):
            return str(binding.network.bind_address)
        return None

    @property
    def is_related(self) -> bool:
        """Check if the watcher is related to any PostgreSQL cluster."""
        relations = self.model.relations.get(WATCHER_RELATION, [])
        return len(relations) > 0

    # -- Port allocation --

    def _load_port_allocations(self) -> dict[str, int]:
        """Load port allocations from persistent file.

        Returns:
            Dictionary mapping relation_id (as string) to port number.
        """
        port_path = Path(PORTS_FILE)
        if port_path.exists():
            try:
                return json.loads(port_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load port allocations: {e}")
        return {}

    def _save_port_allocations(self, allocations: dict[str, int]) -> None:
        """Save port allocations to persistent file."""
        Path(PORTS_FILE).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(PORTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(allocations))

    def _get_port_for_relation(self, relation_id: int) -> int:
        """Get or assign a port for a given relation ID.

        Args:
            relation_id: The Juju relation ID.

        Returns:
            The assigned port number.
        """
        allocations = self._load_port_allocations()
        key = str(relation_id)

        if key in allocations:
            return allocations[key]

        # Assign next available port starting from RAFT_PORT
        used_ports = set(allocations.values())
        port = RAFT_PORT
        while port in used_ports:
            port += 1

        allocations[key] = port
        self._save_port_allocations(allocations)
        logger.info(f"Assigned port {port} to relation {relation_id}")
        return port

    def _release_port_for_relation(self, relation_id: int) -> None:
        """Release the port allocated for a relation.

        Args:
            relation_id: The Juju relation ID.
        """
        allocations = self._load_port_allocations()
        key = str(relation_id)
        if key in allocations:
            port = allocations.pop(key)
            self._save_port_allocations(allocations)
            logger.info(f"Released port {port} from relation {relation_id}")

    # -- Per-relation RaftController management --

    def _get_or_create_raft_controller(self, relation_id: int) -> RaftController:
        """Get or create a RaftController for the given relation.

        Args:
            relation_id: The Juju relation ID.

        Returns:
            The RaftController instance for this relation.
        """
        if relation_id not in self._raft_controllers:
            from raft_controller import RaftController

            instance_id = f"rel{relation_id}"
            self._raft_controllers[relation_id] = RaftController(
                self.charm, instance_id=instance_id
            )
        return self._raft_controllers[relation_id]

    # -- Per-relation helpers --

    def _get_raft_password(self, relation: Relation) -> str | None:
        """Get the Raft password from the relation secret.

        Args:
            relation: The specific watcher relation.
        """
        if not relation.app:
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

    def get_watcher_password(self, relation: Relation) -> str | None:
        """Get the watcher PostgreSQL user password from the relation secret.

        Args:
            relation: The specific watcher relation.
        """
        if not relation.app:
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

    def _get_pg_endpoints(self, relation: Relation) -> list[str]:
        """Get PostgreSQL endpoints from the relation.

        Args:
            relation: The specific watcher relation.
        """
        if not relation.app:
            return []

        pg_endpoints_json = relation.data[relation.app].get("pg-endpoints")
        if not pg_endpoints_json:
            return []

        try:
            return json.loads(pg_endpoints_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse pg-endpoints JSON")
            return []

    def _get_raft_partner_addrs(self, relation: Relation) -> list[str]:
        """Get Raft partner addresses from the relation.

        Args:
            relation: The specific watcher relation.
        """
        if not relation.app:
            return []

        raft_addrs_json = relation.data[relation.app].get("raft-partner-addrs")
        if not raft_addrs_json:
            return []

        try:
            return json.loads(raft_addrs_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse raft-partner-addrs JSON")
            return []

    def _get_cluster_name(self, relation: Relation) -> str:
        """Get the cluster name from the relation app data.

        Args:
            relation: The specific watcher relation.

        Returns:
            The cluster name, or a fallback label.
        """
        if relation.app:
            name = relation.data[relation.app].get("cluster-name")
            if name:
                return name
        return f"relation-{relation.id}"

    # -- Lifecycle events --

    @staticmethod
    def _is_snap_installed() -> bool:
        """Check if the charmed-postgresql snap is installed."""
        try:
            from charmlibs import snap

            cache = snap.SnapCache()
            return cache[SNAP_NAME].present
        except Exception:
            return False

    def _on_install(self, event: InstallEvent) -> None:
        """Install watcher components.

        Installs the charmed-postgresql snap from the snap store to get
        Patroni's ``patroni_raft_controller`` binary, which is used as
        the Raft voter. PostgreSQL services are not started.
        """
        if self._is_snap_installed():
            logger.info(f"{SNAP_NAME} snap already installed, skipping")
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return

        self.charm.unit.status = MaintenanceStatus("Installing pysyncobj")

        try:
            from charmlibs import snap

            cache = snap.SnapCache()
            snap_package = cache[SNAP_NAME]
            snap_package.ensure(snap.SnapState.Present, channel=SNAP_CHANNEL)
            snap_package.hold()
            logger.info(f"{SNAP_NAME} snap installed from channel {SNAP_CHANNEL}")
        except Exception as e:
            logger.error(f"Failed to install {SNAP_NAME} snap: {e}")
            event.defer()
            return

        self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
        logger.info("Watcher mode install complete")

    def _on_start(self, event: StartEvent) -> None:
        """Handle start event in watcher mode."""
        if not self.is_related:
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return
        # Don't set ActiveStatus here -- let _on_update_status promote to Active
        # once Raft is actually connected
        self.charm.unit.status = WaitingStatus("Starting Raft connection")

    def _update_unit_address_if_changed(self) -> None:
        """Update unit-address in relation data if IP has changed, for ALL relations."""
        new_address = self.unit_ip
        if not new_address:
            return

        for relation in self.model.relations.get(WATCHER_RELATION, []):
            current_address = relation.data[self.charm.unit].get("unit-address")
            if current_address == new_address:
                continue

            logger.info(
                f"Unit IP changed from {current_address} to {new_address} "
                f"in relation {relation.id}, updating relation data"
            )
            relation.data[self.charm.unit]["unit-address"] = new_address

            port = self._get_port_for_relation(relation.id)
            raft_password = self._get_raft_password(relation)
            partner_addrs = self._get_raft_partner_addrs(relation)
            if raft_password and partner_addrs:
                raft_controller = self._get_or_create_raft_controller(relation.id)
                changed = raft_controller.configure(
                    self_addr=f"{new_address}:{port}",
                    partner_addrs=[f"{addr}:{RAFT_PORT}" for addr in partner_addrs],
                    password=raft_password,
                )
                if changed and raft_controller.is_running():
                    logger.info(
                        f"Restarting Raft controller for relation {relation.id} due to IP change"
                    )
                    raft_controller.restart()

    def _on_update_status(self, event: UpdateStatusEvent) -> None:
        """Handle update status event in watcher mode."""
        relations = self.model.relations.get(WATCHER_RELATION, [])
        if not relations:
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return

        self._update_unit_address_if_changed()

        connected_count = 0
        total_endpoints = 0
        az_warnings: list[str] = []
        info_warnings: list[str] = []

        for relation in relations:
            raft_controller = self._get_or_create_raft_controller(relation.id)
            raft_status = raft_controller.get_status()
            if raft_status.get("connected"):
                connected_count += 1

            pg_endpoints = self._get_pg_endpoints(relation)
            total_endpoints += len(pg_endpoints)

            if len(pg_endpoints) % 2 != 0:
                cluster_name = self._get_cluster_name(relation)
                info_warnings.append(
                    f"WARNING: cluster '{cluster_name}' has {len(pg_endpoints)} units (odd);"
                    " adding a watcher creates even Raft membership,"
                    " which degrades partition tolerance"
                )

            az_warning = self._check_az_colocation(relation)
            if az_warning:
                az_warnings.append(az_warning)

        if connected_count == 0:
            self.charm.unit.status = WaitingStatus("Connecting to Raft cluster")
            return

        cluster_count = len(relations)
        if cluster_count == 1:
            msg = f"Raft connected, monitoring {total_endpoints} PostgreSQL endpoints"
        else:
            msg = (
                f"Raft connected to {connected_count}/{cluster_count} clusters, "
                f"monitoring {total_endpoints} PostgreSQL endpoints"
            )

        # AZ co-location blocks in production; odd-count warnings never block
        if az_warnings and self.charm.config.profile == "production":
            self.charm.unit.status = BlockedStatus("AZ co-location: " + "; ".join(az_warnings))
            return

        all_warnings = az_warnings + info_warnings
        if all_warnings:
            msg += "; " + "; ".join(all_warnings)

        self.charm.unit.status = ActiveStatus(msg)

    def _check_az_colocation(self, relation: Relation) -> str | None:
        """Check if the watcher is in the same AZ as any PostgreSQL unit.

        Args:
            relation: The specific watcher relation.

        Returns:
            Warning message if co-located, None otherwise.
        """
        watcher_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        if not watcher_az:
            return None

        colocated_units = []
        for unit in relation.units:
            unit_az = relation.data[unit].get("unit-az")
            if unit_az and unit_az == watcher_az:
                colocated_units.append(unit.name)

        if colocated_units:
            return f"WARNING: watcher shares AZ '{watcher_az}' with {', '.join(colocated_units)}"
        return None

    # -- Relation events --

    def _on_watcher_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle watcher relation joined event."""
        logger.info(f"Joined watcher relation {event.relation.id} with PostgreSQL cluster")
        if unit_ip := self.unit_ip:
            event.relation.data[self.charm.unit]["unit-address"] = unit_ip
        unit_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        if unit_az:
            event.relation.data[self.charm.unit]["unit-az"] = unit_az

    def _on_watcher_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle watcher relation changed event."""
        relation = event.relation
        logger.info(f"Watcher relation {relation.id} data changed")

        raft_password = self._get_raft_password(relation)
        if not raft_password:
            logger.debug("Raft password not yet available")
            event.defer()
            return

        partner_addrs = self._get_raft_partner_addrs(relation)
        if not partner_addrs:
            logger.debug("Raft partner addresses not yet available")
            event.defer()
            return

        unit_ip = self.unit_ip
        if not unit_ip:
            logger.debug("Unit IP not available yet")
            event.defer()
            return

        # Get or assign a port for this relation
        port = self._get_port_for_relation(relation.id)

        raft_controller = self._get_or_create_raft_controller(relation.id)
        changed = raft_controller.configure(
            self_addr=f"{unit_ip}:{port}",
            partner_addrs=[f"{addr}:{RAFT_PORT}" for addr in partner_addrs],
            password=raft_password,
        )

        if raft_controller.is_running():
            if changed:
                logger.info(
                    f"Restarting Raft controller for relation {relation.id} "
                    "to apply config changes"
                )
                raft_controller.restart()
        else:
            logger.info(f"Starting Raft controller service for relation {relation.id}")
            raft_controller.start()

        relation.data[self.charm.unit]["unit-address"] = unit_ip
        relation.data[self.charm.unit]["watcher-raft-port"] = str(port)
        unit_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        if unit_az:
            relation.data[self.charm.unit]["unit-az"] = unit_az
        # Only set raft-status and ActiveStatus after verifying the service is running
        if raft_controller.is_running():
            relation.data[self.charm.unit]["raft-status"] = "connected"
            # Check AZ co-location and enforce based on profile
            az_warning = self._check_az_colocation(relation)
            if az_warning and self.charm.config.profile == "production":
                self.charm.unit.status = BlockedStatus(f"AZ co-location: {az_warning}")
            else:
                self.charm.unit.status = ActiveStatus()
        else:
            self.charm.unit.status = WaitingStatus("Raft controller not running")

    def _on_watcher_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle watcher relation departed event."""
        logger.info(f"PostgreSQL unit departed from watcher relation {event.relation.id}")

    def _on_watcher_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle watcher relation broken event."""
        relation_id = event.relation.id
        logger.info(f"Watcher relation {relation_id} broken")

        # Stop and clean up the Raft controller for this relation
        if relation_id in self._raft_controllers:
            self._raft_controllers[relation_id].stop()
            del self._raft_controllers[relation_id]
        else:
            # Try to stop via a fresh controller in case we were recreated
            from raft_controller import RaftController

            controller = RaftController(self.charm, instance_id=f"rel{relation_id}")
            controller.stop()

        # Release the port allocation
        self._release_port_for_relation(relation_id)

        # Check if any relations remain
        remaining = [
            r for r in self.model.relations.get(WATCHER_RELATION, []) if r.id != relation_id
        ]
        if not remaining:
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")

    # -- Actions --

    def _build_ip_maps(self, relation: Relation) -> tuple[dict[str, str], dict[str, str]]:
        """Build IP-to-AZ and IP-to-unit-name maps from relation data.

        Returns:
            Tuple of (ip_to_az, ip_to_unit) dictionaries.
        """
        ip_to_az: dict[str, str] = {}
        ip_to_unit: dict[str, str] = {}
        for unit in relation.units:
            unit_ip = relation.data[unit].get("unit-address")
            if unit_ip:
                ip_to_unit[unit_ip] = unit.name
            unit_az = relation.data[unit].get("unit-az")
            if unit_ip and unit_az:
                ip_to_az[unit_ip] = unit_az
        watcher_ip = self.unit_ip
        if watcher_ip:
            ip_to_unit[watcher_ip] = self.charm.unit.name
        return ip_to_az, ip_to_unit

    def _resolve_raft_members(
        self, raft_status: dict[str, Any], ip_to_unit: dict[str, str]
    ) -> None:
        """Resolve Raft member IPs to unit names in-place."""
        resolved = []
        for member_addr in raft_status.get("members", []):
            member_ip = member_addr.split(":")[0]
            resolved.append(ip_to_unit.get(member_ip, member_addr))
        raft_status["members"] = sorted(resolved)

    def _build_cluster_topology(self, relation: Relation) -> dict[str, Any]:
        """Build topology information for a single cluster relation."""
        cluster_name = self._get_cluster_name(relation)
        pg_endpoints = self._get_pg_endpoints(relation)
        ip_to_az, ip_to_unit = self._build_ip_maps(relation)

        endpoint_entries: list[dict[str, Any]] = []
        for endpoint in pg_endpoints:
            entry: dict[str, Any] = {"ip": endpoint}
            if endpoint in ip_to_az:
                entry["az"] = ip_to_az[endpoint]
            endpoint_entries.append(entry)

        raft_controller = self._get_or_create_raft_controller(relation.id)
        raft_status = raft_controller.get_status()
        self._resolve_raft_members(raft_status, ip_to_unit)

        if pg_endpoints:
            from watcher_health import HealthChecker

            health_checker = HealthChecker(
                self.charm,
                password_getter=lambda rel=relation: self.get_watcher_password(rel),
            )
            health_results = health_checker.check_all_endpoints(pg_endpoints)
            for i, endpoint in enumerate(pg_endpoints):
                if i < len(endpoint_entries):
                    endpoint_entries[i]["healthy"] = health_results.get(endpoint, False)

        return {
            "cluster_name": cluster_name,
            "relation_id": relation.id,
            "postgresql_endpoints": endpoint_entries,
            "raft_status": raft_status,
        }

    def _on_show_topology(self, event: ActionEvent) -> None:
        """Handle show-topology action."""
        watcher_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        watcher_info: dict[str, Any] = {
            "unit": self.charm.unit.name,
            "ip": self.unit_ip,
        }
        if watcher_az:
            watcher_info["az"] = watcher_az

        clusters = [
            self._build_cluster_topology(relation)
            for relation in self.model.relations.get(WATCHER_RELATION, [])
        ]

        topology: dict[str, Any] = {
            "watcher": watcher_info,
            "clusters": clusters,
        }

        event.set_results({"topology": json.dumps(topology, indent=2)})

    def _on_trigger_health_check(self, event: ActionEvent) -> None:
        """Handle trigger-health-check action."""
        clusters: list[dict[str, Any]] = []
        total_healthy = 0
        total_count = 0

        for relation in self.model.relations.get(WATCHER_RELATION, []):
            pg_endpoints = self._get_pg_endpoints(relation)
            if not pg_endpoints:
                continue

            from watcher_health import HealthChecker

            health_checker = HealthChecker(
                self.charm,
                password_getter=lambda rel=relation: self.get_watcher_password(rel),
            )
            health_results = health_checker.check_all_endpoints(pg_endpoints)

            _ip_to_az, ip_to_unit = self._build_ip_maps(relation)

            cluster_name = self._get_cluster_name(relation)
            endpoint_statuses: dict[str, str] = {}
            for endpoint, healthy in health_results.items():
                unit_name = ip_to_unit.get(endpoint)
                label = unit_name if unit_name else f"{cluster_name}/{endpoint}"
                endpoint_statuses[label] = "healthy" if healthy else "unhealthy"
                if healthy:
                    total_healthy += 1
                total_count += 1

            clusters.append({
                "cluster_name": cluster_name,
                "endpoints": endpoint_statuses,
            })

        if total_count == 0:
            event.fail("No PostgreSQL endpoints available")
            return

        output: dict[str, Any] = {
            "clusters": clusters,
            "healthy-count": total_healthy,
            "total-count": total_count,
        }

        event.set_results({"health-check": json.dumps(output, indent=2)})
