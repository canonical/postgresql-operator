# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Watcher Requirer Relation implementation.

This module handles the watcher (requirer) side of the relation, used when the
charm is deployed with role=watcher. It connects to one or more PostgreSQL
applications (which provide the watcher-offer relation) and participates in
Raft consensus as a lightweight witness for stereo mode (2-node clusters).

Multi-cluster support:
- Each watcher relation gets its own RaftController instance
- Ports are assigned dynamically starting from RAFT_PORT (2223) and persisted
  in a port allocation file at /var/snap/charmed-postgresql/common/watcher-raft/ports.json
- Each RaftController uses instance-specific data directories and systemd services
"""

import json
import logging
import os
import typing
from datetime import datetime

from charmlibs.systemd import service_running
from ops import (
    ActiveStatus,
    BlockedStatus,
    InstallEvent,
    MaintenanceStatus,
    Object,
    Relation,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationJoinedEvent,
    SecretChangedEvent,
    SecretNotFoundError,
    StartEvent,
    UpdateStatusEvent,
    WaitingStatus,
)

from constants import RAFT_PORT, WATCHER_RELATION
from raft_controller import RaftController, install_service

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

        # Lifecycle events
        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.leader_elected, self._on_leader_elected)
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
            self.charm.on.secret_changed,
            self._on_watcher_relation_changed,
        )
        self.framework.observe(
            self.charm.on[WATCHER_RELATION].relation_broken,
            self._on_watcher_relation_broken,
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
        if "port-allocations" in self.charm.app_peer_data:
            return json.loads(self.charm.app_peer_data["port-allocations"])
        return {}

    def _save_port_allocations(self, allocations: dict[str, int]) -> None:
        """Save port allocations to persistent file."""
        self.charm.app_peer_data["port-allocations"] = json.dumps(allocations)

    def _is_disabled(self, relation: Relation) -> bool:
        """Is disabled flag set."""
        if not relation:
            return False
        return "disable-watcher" in relation.data[relation.app]

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
        port = RAFT_PORT + 1
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
        if not relation.app or not (
            secret_id := relation.data[relation.app].get("raft-secret-id")
        ):
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
        if not relation.app or not (
            secret_id := relation.data[relation.app].get("raft-secret-id")
        ):
            return None

        try:
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content(refresh=True)
            return content.get("watcher-password")
        except SecretNotFoundError:
            logger.warning(f"Secret {secret_id} not found")
            return None

    def _get_raft_partner_addrs(self, relation: Relation) -> list[str]:
        """Get Raft partner addresses from the relation.

        Args:
            relation: The specific watcher relation.
        """
        if not relation.app or not (
            raft_addrs_json := relation.data[relation.app].get("raft-partner-addrs")
        ):
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
        if relation.app and (name := relation.data[relation.app].get("cluster-name")):
            return name
        return f"relation-{relation.id}"

    def _get_patroni_cas(self, relation: Relation) -> str | None:
        if relation.app and (name := relation.data[relation.app].get("patroni-cas")):
            return name
        return f"relation-{relation.id}"

    def _get_standby_clusters(self, relation: Relation) -> list[str]:
        """Get related standby clusters from the relation app data.

        Args:
            relation: The specific watcher relation.

        Returns:
            A list of standby cluster names.
        """
        if not relation.app or not (
            standby_clusters_json := relation.data[relation.app].get("standby-clusters")
        ):
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

    def _on_leader_elected(self, _) -> None:
        self._update_unit_address_if_changed()

    def _update_unit_address_if_changed(self) -> None:
        """Update unit-address in relation data if IP has changed, for ALL relations."""
        if not (new_address := self.unit_ip):
            return

        unit_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        for relation in self.model.relations.get(WATCHER_RELATION, []):
            current_address = relation.data[self.charm.unit].get("unit-address")
            current_az = relation.data[self.charm.app].get("unit-az")
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
                relation.data[self.charm.app]["unit-az"] = str(unit_az)

            if (
                address_changed
                and (raft_password := self._get_raft_password(relation))
                and (partner_addrs := self._get_raft_partner_addrs(relation))
            ):
                port = self._get_port_for_relation(relation.id)
                raft_controller = RaftController(self.charm, f"rel{relation.id}")
                changed = raft_controller.configure(
                    port,
                    new_address,
                    partner_addrs,
                    raft_password,
                    self._get_patroni_cas(relation),
                )
                if changed and service_running(raft_controller.service_name):
                    logger.info(
                        f"Restarting Raft controller for relation {relation.id} due to IP change"
                    )
                    raft_controller.restart()
                for stale_addr in raft_controller.get_stale_watchers(
                    new_address, raft_password, partner_addrs, port
                ):
                    raft_controller.remove_raft_member(stale_addr, raft_password, partner_addrs)

    def _on_update_status(self, event: UpdateStatusEvent) -> None:
        """Handle update status event in watcher mode."""
        if not self.charm.unit.is_leader():
            if self.charm._peers and len(self.charm._peers.units) > 0:
                self.charm.unit.status = BlockedStatus("Multiple watcher units. One expected.")
            event.defer()
            return

        if not (relations := self.model.relations.get(WATCHER_RELATION, [])):
            self.charm.unit.status = WaitingStatus("Waiting for relation to PostgreSQL")
            return

        self._update_unit_address_if_changed()

        connected_count = 0
        disabled = False
        total_endpoints = 0
        az_warnings: list[str] = []
        info_warnings: list[str] = []

        for relation in relations:
            port = self._get_port_for_relation(relation.id)
            password = self._get_raft_password(relation)
            raft_controller = RaftController(self.charm, instance_id=f"rel{relation.id}")
            raft_status = raft_controller.get_status(port, password)
            disabled = disabled or self._is_disabled(relation)
            connected_count += 1 if raft_status.get("connected") else 0

            pg_endpoints = self._get_raft_partner_addrs(relation)
            total_endpoints += len(pg_endpoints)
            partner_addrs = self._get_raft_partner_addrs(relation)

            if password and not self._should_watcher_vote(partner_addrs):
                cluster_name = self._get_cluster_name(relation)
                raft_controller.remove_raft_member(
                    f"{self.unit_ip}:{port}", password, pg_endpoints
                )
                info_warnings.append(
                    f"WARNING: cluster '{cluster_name}' has odd number units;"
                    " adding a watcher creates even Raft membership,"
                    " which degrades partition tolerance"
                )
                raft_controller.remove_service()
                disabled = True

            az_warning = self._check_az_colocation(relation)
            if az_warning:
                az_warnings.append(az_warning)

        if connected_count == 0 and not disabled:
            self.charm.unit.status = WaitingStatus("Connecting to Raft cluster")
            return

        cluster_count = len(relations)
        msg = (
            f"Raft connected, monitoring {total_endpoints} PostgreSQL endpoints"
            if cluster_count == 1
            else (
                f"Raft connected to {connected_count}/{cluster_count} clusters, "
                f"monitoring {total_endpoints} PostgreSQL endpoints"
            )
        )

        # AZ co-location blocks in production; odd-count warnings never block
        if az_warnings and self.charm.config.profile == "production":
            self.charm.unit.status = BlockedStatus("AZ co-location: " + "; ".join(az_warnings))
            return

        if all_warnings := az_warnings + info_warnings:
            msg += "; " + "; ".join(all_warnings)

        self.charm.unit.status = ActiveStatus(msg)

    def _check_az_colocation(self, relation: Relation) -> str | None:
        """Check if the watcher is in the same AZ as any PostgreSQL unit.

        Args:
            relation: The specific watcher relation.

        Returns:
            Warning message if co-located, None otherwise.
        """
        if not (watcher_az := os.environ.get("JUJU_AVAILABILITY_ZONE")):
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
        if not self.charm.unit.is_leader():
            if self.charm._peers and len(self.charm._peers.units) > 0:
                self.charm.unit.status = BlockedStatus("Multiple watcher units. One expected.")
            event.defer()
            return

        logger.info(f"Joined watcher relation {event.relation.id} with PostgreSQL cluster")
        if unit_ip := self.unit_ip:
            event.relation.data[self.charm.unit]["unit-address"] = unit_ip
        unit_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        if unit_az:
            event.relation.data[self.charm.app]["unit-az"] = unit_az

    def _should_watcher_vote(self, partner_addrs: list[str]) -> bool:
        pg_num = len(partner_addrs)
        return pg_num < 3 or pg_num % 2 == 0

    def _on_watcher_relation_changed(
        self, event: RelationChangedEvent | SecretChangedEvent
    ) -> None:
        """Handle watcher relation changed event."""
        if not self.charm.unit.is_leader():
            return

        if self.charm._peers is None or not (unit_ip := self.unit_ip):
            logger.debug("Deferring watcher relation: Peer relation not yet joined")
            event.defer()
            return

        relations = (
            [event.relation]
            if isinstance(event, RelationChangedEvent)
            else self.model.relations.get(WATCHER_RELATION, [])
        )
        for relation in relations:
            logger.info(f"Watcher relation {relation.id} data changed")

            if not (raft_password := self._get_raft_password(relation)) or not (
                partner_addrs := self._get_raft_partner_addrs(relation)
            ):
                logger.debug("Raft details are not yet available")
                return

            # Get or assign a port for this relation
            port = self._get_port_for_relation(relation.id)

            raft_controller = RaftController(self.charm, f"rel{relation.id}")
            if self._is_disabled(relation) or not self._should_watcher_vote(partner_addrs):
                logger.debug("Disabling the watcher")
                raft_controller.remove_service()
                raft_controller.remove_raft_member(
                    f"{self.unit_ip}:{port}", raft_password, partner_addrs
                )
                relation.data[self.charm.app]["raft-status"] = "disabled"
                return

            if raft_controller.configure(
                port, unit_ip, partner_addrs, raft_password, self._get_patroni_cas(relation)
            ):
                logger.info(
                    f"Restarting Raft controller for relation {relation.id} to apply config changes"
                )
                raft_controller.restart()

            relation.data[self.charm.unit]["unit-address"] = unit_ip
            relation.data[self.charm.app]["watcher-raft-port"] = str(port)
            if unit_az := os.environ.get("JUJU_AVAILABILITY_ZONE"):
                relation.data[self.charm.app]["unit-az"] = unit_az
            # Only set raft-status and ActiveStatus after verifying the service is running
            if service_running(raft_controller.service_name):
                relation.data[self.charm.app]["raft-status"] = "connected"
                # Check AZ co-location and enforce based on profile
                if (
                    az_warning := self._check_az_colocation(relation)
                ) and self.charm.config.profile == "production":
                    self.charm.unit.status = BlockedStatus(f"AZ co-location: {az_warning}")
                else:
                    self.charm.unit.status = ActiveStatus()
            else:
                self.charm.unit.status = WaitingStatus("Raft controller not running")

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
