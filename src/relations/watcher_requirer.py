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

import json
import logging
import os
import typing
from datetime import datetime
from typing import Any

from charmlibs.systemd import service_running
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

from constants import RAFT_PORT, WATCHER_RELATION
from raft_controller import ClusterStatus, RaftController, install_service

if typing.TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

SNAP_NAME = "charmed-postgresql"
SNAP_CHANNEL = "16/edge"


class WatcherRequirerHandler(Object):
    """Handles the watcher requirer relation and watcher-mode lifecycle."""

    def __init__(self, charm: "PostgresqlOperatorCharm"):
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
        self.framework.observe(
            self.charm.on.get_cluster_status_action, self._on_get_cluster_status
        )
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
        if "port_allocations" in self.charm.app_peer_data:
            try:
                return json.loads(self.charm.app_peer_data["port_allocations"])
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to load port allocations: {e}")
        return {}

    def _save_port_allocations(self, allocations: dict[str, int]) -> None:
        """Save port allocations to persistent file."""
        self.charm.app_peer_data["port_allocations"] = json.dumps(allocations)

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

    def _get_standby_clusters(self, relation: Relation) -> list[str]:
        """Get related standby clusters from the relation app data.

        Args:
            relation: The specific watcher relation.

        Returns:
            A list of standby cluster names.
        """
        if not relation.app:
            return []

        standby_clusters_json = relation.data[relation.app].get("standby-clusters")
        if not standby_clusters_json:
            return []

        try:
            return json.loads(standby_clusters_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse standby-clusters JSON")
            return []

    # -- Lifecycle events --

    def _on_install(self, event: InstallEvent) -> None:
        """Install prerequisites for the application."""
        logger.debug("Install start time: %s", datetime.now())

        self.charm.set_unit_status(MaintenanceStatus("installing RAFT controller"))

        # Install the charmed PostgreSQL snap.
        self.charm._install_snap_package(revision=None)
        install_service()

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

        unit_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        for relation in self.model.relations.get(WATCHER_RELATION, []):
            current_address = relation.data[self.charm.unit].get("unit-address")
            current_az = relation.data[self.charm.unit].get("unit-az")
            address_changed = current_address != new_address
            az_changed = bool(unit_az and current_az != unit_az)

            if not address_changed and not az_changed:
                continue

            if address_changed:
                logger.info(
                    f"Unit IP changed from {current_address} to {new_address} "
                    f"in relation {relation.id}, updating relation data"
                )
                relation.data[self.charm.unit]["unit-address"] = new_address

            if az_changed:
                relation.data[self.charm.unit]["unit-az"] = str(unit_az)

            if (
                address_changed
                and (raft_password := self._get_raft_password(relation))
                and (partner_addrs := self._get_raft_partner_addrs(relation))
            ):
                port = self._get_port_for_relation(relation.id)
                raft_controller = RaftController(self.charm, f"rel{relation.id}")
                changed = raft_controller.configure(
                    port, new_address, partner_addrs, raft_password
                )
                if changed and service_running(raft_controller.service_name):
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
            port = self._get_port_for_relation(relation.id)
            password = self._get_raft_password(relation)
            raft_controller = RaftController(self.charm, instance_id=f"rel{relation.id}")
            raft_status = raft_controller.get_status(port, password)
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

        if self.charm._peers is None:
            logger.debug("Deferring watcher relation: Peer relation not yet joined")
            event.defer()
            return

        raft_password = self._get_raft_password(relation)
        if not raft_password:
            logger.debug("Raft password not yet available")
            return

        partner_addrs = self._get_raft_partner_addrs(relation)
        if not partner_addrs:
            logger.debug("Raft partner addresses not yet available")
            return

        unit_ip = self.unit_ip
        if not unit_ip:
            logger.debug("Unit IP not available yet")
            return

        # Get or assign a port for this relation
        port = self._get_port_for_relation(relation.id)

        raft_controller = RaftController(self.charm, f"rel{relation.id}")
        changed = raft_controller.configure(port, unit_ip, partner_addrs, raft_password)

        if service_running(raft_controller.service_name):
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
        if service_running(raft_controller.service_name):
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
        controller = RaftController(self.charm, instance_id=f"rel{relation_id}")
        controller.remove_service()

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
        self, raft_status: ClusterStatus, ip_to_unit: dict[str, str]
    ) -> None:
        """Resolve Raft member IPs to unit names in-place."""
        resolved = []
        for member_addr in raft_status.get("members", []):
            member_ip = member_addr.split(":")[0]
            resolved.append(ip_to_unit.get(member_ip, member_addr))
        raft_status["members"] = sorted(resolved)

    def _on_get_cluster_status(self, event: ActionEvent) -> None:
        """Handle get-cluster-status action."""
        cluster_name_filter = event.params.get("cluster-name")
        cluster_set_mode = event.params.get("standby-clusters", False)

        relations = self.model.relations.get(WATCHER_RELATION, [])
        clusters_data: dict[str, dict[str, Any]] = {}
        standby_clusters_map: dict[str, list[str]] = {}
        for relation in relations:
            cluster_name = self._get_cluster_name(relation)
            if cluster_name_filter and cluster_name != cluster_name_filter:
                continue
            clusters_data[cluster_name] = self._format_cluster_status(relation)
            standby_clusters_map[cluster_name] = self._get_standby_clusters(relation)

        if not clusters_data:
            if cluster_name_filter:
                event.fail(f"Cluster '{cluster_name_filter}' not found among related clusters.")
            else:
                event.set_results({"success": "True", "status": json.dumps({})})
            return

        if cluster_set_mode:
            result_status = self._format_cluster_set_status(clusters_data, standby_clusters_map)
        elif len(clusters_data) == 1:
            # Single cluster: return the cluster status directly
            result_status = next(iter(clusters_data.values()))
        else:
            # Multi-cluster: return list with watcher summary
            result_status = {
                "clusters": list(clusters_data.values()),
                "watcher": {
                    "unit": self.charm.unit.name,
                    "address": self.unit_ip,
                    "clusters_monitored": len(clusters_data),
                },
            }

        event.set_results({"success": "True", "status": json.dumps(result_status)})

    def _get_watcher_voting(self, relation: Relation, raft_status: ClusterStatus) -> bool:
        """Return whether the watcher should be shown as voting."""
        if not relation.app:
            return raft_status.get("connected", False)

        watcher_voting_str = relation.data[relation.app].get("watcher-voting")
        if watcher_voting_str is None:
            return raft_status.get("connected", False)
        return watcher_voting_str == "true"

    def _get_member_lag_by_endpoint(self, relation: Relation) -> dict[str, Any]:
        """Return per-endpoint lag data from relation application data."""
        if not relation.app:
            return {}

        member_lag_raw = relation.data[relation.app].get("member-lag", "{}")
        if not isinstance(member_lag_raw, str):
            return {}

        try:
            parsed_member_lag = json.loads(member_lag_raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse member-lag JSON")
            return {}

        if isinstance(parsed_member_lag, dict):
            return parsed_member_lag
        return {}

    @staticmethod
    def _cluster_role_from_health(saw_healthy_member: bool, saw_primary_member: bool) -> str:
        """Return the inferred cluster role from endpoint health results."""
        if saw_primary_member:
            return "primary"
        if saw_healthy_member:
            return "standby"
        return "unknown"

    def _build_postgresql_topology(
        self,
        relation: Relation,
        pg_endpoints: list[str],
        ip_to_unit: dict[str, str],
    ) -> tuple[dict[str, Any], str | None, str]:
        """Build PostgreSQL topology entries and infer the cluster role."""
        topology: dict[str, Any] = {}
        primary_endpoint = None
        saw_healthy_member = False
        saw_primary_member = False
        member_lag_by_endpoint = self._get_member_lag_by_endpoint(relation)

        if not pg_endpoints:
            return topology, primary_endpoint, "unknown"

        from watcher_health import HealthChecker

        health_checker = HealthChecker(
            self.charm,
            password_getter=lambda rel=relation: self.get_watcher_password(rel),
        )
        health_results = health_checker.check_all_endpoints(pg_endpoints)

        for endpoint in pg_endpoints:
            unit_name = ip_to_unit.get(endpoint, endpoint)
            res = health_results.get(endpoint, {})
            is_healthy = res.get("healthy", False)
            is_primary = not res.get("is_in_recovery", True)

            if is_healthy:
                saw_healthy_member = True
            if is_primary:
                primary_endpoint = f"{endpoint}:5432"
            if is_healthy and is_primary:
                saw_primary_member = True

            topology[unit_name] = {
                "address": f"{endpoint}:5432",
                "memberrole": "primary" if is_primary else "sync_standby",
                "mode": "r/w" if is_primary else "r/o",
                "status": "online" if is_healthy else "offline",
                "version": self._get_pg_version(),
                "lag": member_lag_by_endpoint.get(endpoint, 0),
            }

        cluster_role = self._cluster_role_from_health(saw_healthy_member, saw_primary_member)
        return topology, primary_endpoint, cluster_role

    def _is_tls_enabled(self, relation: Relation) -> bool:
        """Return whether TLS is enabled for the related PostgreSQL cluster."""
        if not relation.app:
            return False
        return relation.data[relation.app].get("tls-enabled", "false") == "true"

    def _get_timeline(self, relation: Relation) -> int:
        """Return the related PostgreSQL timeline from relation data."""
        if not relation.app:
            return 0

        timeline_str = relation.data[relation.app].get("timeline", "0")
        try:
            return int(timeline_str)
        except (ValueError, TypeError):
            return 0

    def _format_cluster_status(self, relation: Relation) -> dict[str, Any]:
        """Format cluster status for a single cluster relation."""
        cluster_name = self._get_cluster_name(relation)
        pg_endpoints = self._get_pg_endpoints(relation)
        _ip_to_az, ip_to_unit = self._build_ip_maps(relation)

        # Get Raft status
        port = self._get_port_for_relation(relation.id)
        password = self._get_raft_password(relation)
        raft_controller = RaftController(self.charm, instance_id=f"rel{relation.id}")
        raft_status = raft_controller.get_status(port, password)
        self._resolve_raft_members(raft_status, ip_to_unit)
        has_quorum = raft_status.get("has_quorum", False)
        watcher_voting = self._get_watcher_voting(relation, raft_status)
        topology, primary_endpoint, cluster_role = self._build_postgresql_topology(
            relation, pg_endpoints, ip_to_unit
        )

        # Add watcher entry to topology
        watcher_port = self._get_port_for_relation(relation.id)
        watcher_ip = self.unit_ip or relation.data[self.charm.unit].get("unit-address")
        watcher_address = f"{watcher_ip}:{watcher_port}" if watcher_ip else None
        topology[self.charm.unit.name] = {
            "address": watcher_address,
            "memberrole": "watcher",
            "mode": "n/a",
            "status": "online" if raft_status.get("running", False) else "offline",
            "version": "n/a",
            "voting": watcher_voting,
        }

        status_text = (
            "cluster is tolerant to failures."
            if has_quorum
            else "cluster is not tolerant to any failures."
        )

        return {
            "clustername": cluster_name,
            "clusterrole": cluster_role,
            "primary": primary_endpoint,
            "ssl": "required" if self._is_tls_enabled(relation) else "disabled",
            "status": "ok" if has_quorum else "ok_no_tolerance",
            "statustext": status_text,
            "timeline": self._get_timeline(relation),
            "topology": topology,
            "raft": {
                "has_quorum": has_quorum,
                "leader": raft_status.get("leader"),
                "members": raft_status.get("members", []),
            },
        }

    def _format_cluster_set_status(
        self,
        clusters_data: dict[str, dict[str, Any]],
        standby_clusters_map: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Format cluster-set status for async replication view."""
        clusters_summary: dict[str, Any] = {}
        primary_cluster_name = None

        for name, data in clusters_data.items():
            cluster_role = data.get("clusterrole", "unknown")
            is_primary = cluster_role == "primary"
            summary: dict[str, Any] = {
                "clusterrole": cluster_role,
                "status": data.get("status", "unknown"),
                "primary": data.get("primary"),
                "linked_standby_clusters": standby_clusters_map.get(name, []),
            }
            if is_primary and primary_cluster_name is None:
                primary_cluster_name = name
            elif cluster_role == "standby":
                summary["replication_status"] = "streaming"
                summary["replication_lag"] = 0
            summary["timeline"] = data.get("timeline", 0)
            clusters_summary[name] = summary

        all_healthy = all(c.get("status") == "ok" for c in clusters_data.values())

        return {
            "clusters": clusters_summary,
            "primary_cluster": primary_cluster_name,
            "status": "healthy" if all_healthy else "degraded",
            "statustext": ("all clusters available." if all_healthy else "some clusters at risk."),
        }

    def _get_pg_version(self) -> str:
        """Get PostgreSQL version from refresh_versions.toml."""
        try:
            with open("refresh_versions.toml", "rb") as f:
                import tomli

                versions = tomli.load(f)
                return str(versions.get("workload", "unknown"))
        except Exception:
            return "unknown"

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
            for endpoint, res in health_results.items():
                unit_name = ip_to_unit.get(endpoint)
                label = unit_name if unit_name else f"{cluster_name}/{endpoint}"
                is_healthy = res.get("healthy", False) if isinstance(res, dict) else False
                endpoint_statuses[label] = "healthy" if is_healthy else "unhealthy"
                if is_healthy:
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
