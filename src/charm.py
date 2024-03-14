#!/usr/bin/env -S LD_LIBRARY_PATH=lib python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for the PostgreSQL database."""

import json
import logging
import os
import platform
import subprocess
from typing import Dict, List, Literal, Optional, Set, get_args

import psycopg2
from charms.data_platform_libs.v0.data_interfaces import DataPeer, DataPeerUnit
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from charms.operator_libs_linux.v2 import snap
from charms.postgresql_k8s.v0.postgresql import (
    REQUIRED_PLUGINS,
    PostgreSQL,
    PostgreSQLCreateUserError,
    PostgreSQLEnableDisableExtensionError,
    PostgreSQLListUsersError,
    PostgreSQLUpdateUserPasswordError,
)
from charms.postgresql_k8s.v0.postgresql_tls import PostgreSQLTLS
from charms.rolling_ops.v0.rollingops import RollingOpsManager, RunWithLock
from ops import JujuVersion
from ops.charm import (
    ActionEvent,
    HookEvent,
    InstallEvent,
    LeaderElectedEvent,
    RelationDepartedEvent,
    StartEvent,
)
from ops.framework import EventBase
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    Relation,
    Unit,
    WaitingStatus,
)
from tenacity import RetryError, Retrying, retry, stop_after_attempt, stop_after_delay, wait_fixed

from backups import PostgreSQLBackups
from cluster import (
    NotReadyError,
    Patroni,
    RemoveRaftMemberFailedError,
    SwitchoverFailedError,
)
from cluster_topology_observer import (
    ClusterTopologyChangeCharmEvents,
    ClusterTopologyObserver,
)
from config import CharmConfig
from constants import (
    APP_SCOPE,
    BACKUP_USER,
    METRICS_PORT,
    MONITORING_PASSWORD_KEY,
    MONITORING_SNAP_SERVICE,
    MONITORING_USER,
    PATRONI_CONF_PATH,
    PEER,
    POSTGRESQL_SNAP_NAME,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    SECRET_DELETED_LABEL,
    SECRET_INTERNAL_LABEL,
    SECRET_KEY_OVERRIDES,
    SNAP_PACKAGES,
    SYSTEM_USERS,
    TLS_CA_FILE,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    UNIT_SCOPE,
    USER,
    USER_PASSWORD_KEY,
)
from relations.db import EXTENSIONS_BLOCKING_MESSAGE, DbProvides
from relations.postgresql_provider import PostgreSQLProvider
from upgrade import PostgreSQLUpgrade, get_postgresql_dependencies_model
from utils import new_password

logger = logging.getLogger(__name__)

PRIMARY_NOT_REACHABLE_MESSAGE = "waiting for primary to be reachable from this unit"
EXTENSIONS_DEPENDENCY_MESSAGE = "Unsatisfied plugin dependencies. Please check the logs"

Scopes = Literal[APP_SCOPE, UNIT_SCOPE]


class PostgresqlOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charmed Operator for the PostgreSQL database."""

    config_type = CharmConfig
    on = ClusterTopologyChangeCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        self.peer_relation_app = DataPeer(
            self,
            relation_name=PEER,
            additional_secret_fields=[
                "monitoring-password",
                "operator-password",
                "replication-password",
                "rewind-password",
            ],
            secret_field_name=SECRET_INTERNAL_LABEL,
            deleted_label=SECRET_DELETED_LABEL,
        )
        self.peer_relation_unit = DataPeerUnit(
            self,
            relation_name=PEER,
            additional_secret_fields=[
                "key",
                "csr",
                "cauth",
                "cert",
                "chain",
            ],
            secret_field_name=SECRET_INTERNAL_LABEL,
            deleted_label=SECRET_DELETED_LABEL,
        )

        juju_version = JujuVersion.from_environ()
        if juju_version.major > 2:
            run_cmd = "/usr/bin/juju-exec"
        else:
            run_cmd = "/usr/bin/juju-run"
        self._observer = ClusterTopologyObserver(self, run_cmd)
        self.framework.observe(self.on.cluster_topology_change, self._on_cluster_topology_change)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.secret_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.secret_remove, self._on_peer_relation_changed)
        self.framework.observe(self.on[PEER].relation_departed, self._on_peer_relation_departed)
        self.framework.observe(self.on.pgdata_storage_detaching, self._on_pgdata_storage_detaching)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.get_password_action, self._on_get_password)
        self.framework.observe(self.on.set_password_action, self._on_set_password)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.cluster_name = self.app.name
        self._member_name = self.unit.name.replace("/", "-")
        self._storage_path = self.meta.storages["pgdata"].location

        self.upgrade = PostgreSQLUpgrade(
            self,
            model=get_postgresql_dependencies_model(),
            relation_name="upgrade",
            substrate="vm",
        )
        self.postgresql_client_relation = PostgreSQLProvider(self)
        self.legacy_db_relation = DbProvides(self, admin=False)
        self.legacy_db_admin_relation = DbProvides(self, admin=True)
        self.backup = PostgreSQLBackups(self, "s3-parameters")
        self.tls = PostgreSQLTLS(self, PEER)
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )
        self._observer.start_observer()
        self._grafana_agent = COSAgentProvider(
            self,
            metrics_endpoints=[{"path": "/metrics", "port": METRICS_PORT}],
            scrape_configs=self.patroni_scrape_config,
            refresh_events=[
                self.on[PEER].relation_changed,
                self.on.secret_changed,
                self.on.secret_remove,
            ],
            log_slots=[f"{POSTGRESQL_SNAP_NAME}:logs"],
        )

    def patroni_scrape_config(self) -> List[Dict]:
        """Generates scrape config for the Patroni metrics endpoint."""
        return [
            {
                "metrics_path": "/metrics",
                "static_configs": [{"targets": [f"{self._unit_ip}:8008"]}],
                "tls_config": {"insecure_skip_verify": True},
                "scheme": "https" if self.is_tls_enabled else "http",
            }
        ]

    @property
    def app_units(self) -> set[Unit]:
        """The peer-related units in the application."""
        if not self._peers:
            return set()

        return {self.unit, *self._peers.units}

    @property
    def app_peer_data(self) -> Dict:
        """Application peer relation data object."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self.app]

    @property
    def unit_peer_data(self) -> Dict:
        """Unit peer relation data object."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self.unit]

    def _peer_data(self, scope: Scopes) -> Dict:
        """Return corresponding databag for app/unit."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self._scope_obj(scope)]

    def _scope_obj(self, scope: Scopes):
        if scope == APP_SCOPE:
            return self.app
        if scope == UNIT_SCOPE:
            return self.unit

    def _translate_field_to_secret_key(self, key: str) -> str:
        """Change 'key' to secrets-compatible key field."""
        if not JujuVersion.from_environ().has_secrets:
            return key
        key = SECRET_KEY_OVERRIDES.get(key, key)
        new_key = key.replace("_", "-")
        return new_key.strip("-")

    def get_secret(self, scope: Scopes, key: str) -> Optional[str]:
        """Get secret from the secret storage."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        peers = self.model.get_relation(PEER)
        secret_key = self._translate_field_to_secret_key(key)
        if scope == APP_SCOPE:
            value = self.peer_relation_app.fetch_my_relation_field(peers.id, secret_key)
        else:
            value = self.peer_relation_unit.fetch_my_relation_field(peers.id, secret_key)
        return value

    def set_secret(self, scope: Scopes, key: str, value: Optional[str]) -> Optional[str]:
        """Set secret from the secret storage."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        if not value:
            return self.remove_secret(scope, key)

        peers = self.model.get_relation(PEER)
        secret_key = self._translate_field_to_secret_key(key)
        if scope == APP_SCOPE:
            self.peer_relation_app.update_relation_data(peers.id, {secret_key: value})
        else:
            self.peer_relation_unit.update_relation_data(peers.id, {secret_key: value})

    def remove_secret(self, scope: Scopes, key: str) -> None:
        """Removing a secret."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        peers = self.model.get_relation(PEER)
        secret_key = self._translate_field_to_secret_key(key)
        if scope == APP_SCOPE:
            self.peer_relation_app.delete_relation_data(peers.id, [secret_key])
        else:
            self.peer_relation_unit.delete_relation_data(peers.id, [secret_key])

    @property
    def is_cluster_initialised(self) -> bool:
        """Returns whether the cluster is already initialised."""
        return "cluster_initialised" in self.app_peer_data

    @property
    def postgresql(self) -> PostgreSQL:
        """Returns an instance of the object used to interact with the database."""
        return PostgreSQL(
            primary_host=self.primary_endpoint,
            current_host=self._unit_ip,
            user=USER,
            password=self.get_secret(APP_SCOPE, f"{USER}-password"),
            database="postgres",
            system_users=SYSTEM_USERS,
        )

    @property
    def primary_endpoint(self) -> Optional[str]:
        """Returns the endpoint of the primary instance or None when no primary available."""
        if not self._peers:
            logger.debug("primary endpoint early exit: Peer relation not joined yet.")
            return None
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    primary = self._patroni.get_primary()
                    primary_endpoint = self._patroni.get_member_ip(primary)
                    # Force a retry if there is no primary or the member that was
                    # returned is not in the list of the current cluster members
                    # (like when the cluster was not updated yet after a failed switchover).
                    if not primary_endpoint or primary_endpoint not in self._units_ips:
                        # TODO figure out why peer data is not available
                        if (
                            primary_endpoint
                            and len(self._units_ips) == 1
                            and len(self._peers.units) > 1
                        ):
                            logger.warning(
                                "Possibly incoplete peer data: Will not map primary IP to unit IP"
                            )
                            return primary_endpoint
                        logger.debug(
                            "primary endpoint early exit: Primary IP not in cached peer list."
                        )
                        primary_endpoint = None
        except RetryError:
            return None
        else:
            return primary_endpoint

    def get_hostname_by_unit(self, _) -> str:
        """Create a DNS name for a PostgreSQL unit.

        Returns:
            A string representing the hostname of the PostgreSQL unit.
        """
        # For now, as there is no DNS hostnames on VMs, and it would also depend on
        # the underlying provider (LXD, MAAS, etc.), the unit IP is returned.
        return self._unit_ip

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _updated_synchronous_node_count(self, num_units: int = None) -> bool:
        """Tries to update synchronous_node_count configuration and reports the result."""
        try:
            self._patroni.update_synchronous_node_count(num_units)
            return True
        except RetryError:
            logger.debug("Unable to set synchronous_node_count")
            return False

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """The leader removes the departing units from the list of cluster members."""
        # Don't handle this event in the same unit that is departing.
        if event.departing_unit == self.unit:
            logger.debug("Early exit on_peer_relation_departed: Skipping departing unit")
            return

        # Remove the departing member from the raft cluster.
        try:
            departing_member = event.departing_unit.name.replace("/", "-")
            member_ip = self._patroni.get_member_ip(departing_member)
            self._patroni.remove_raft_member(member_ip)
        except RemoveRaftMemberFailedError:
            logger.debug(
                "Deferring on_peer_relation_departed: Failed to remove member from raft cluster"
            )
            event.defer()
            return

        # Allow leader to update the cluster members.
        if not self.unit.is_leader():
            return

        if "cluster_initialised" not in self._peers.data[
            self.app
        ] or not self._updated_synchronous_node_count(len(self._units_ips)):
            logger.debug("Deferring on_peer_relation_departed: cluster not initialized")
            event.defer()
            return

        # Remove cluster members one at a time.
        for member_ip in self._get_ips_to_remove():
            # Check that all members are ready before removing unit from the cluster.
            if not self._patroni.are_all_members_ready():
                logger.info("Deferring reconfigure: another member doing sync right now")
                event.defer()
                return

            # Update the list of the current members.
            self._remove_from_members_ips(member_ip)
            self.update_config()

            if self.primary_endpoint:
                self._update_relation_endpoints()
            else:
                self.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)
                return

    def _on_pgdata_storage_detaching(self, _) -> None:
        # Change the primary if it's the unit that is being removed.
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
        except RetryError:
            # Ignore the event if the primary couldn't be retrieved.
            # If a switchover is needed, an automatic failover will be triggered
            # when the unit is removed.
            logger.debug("Early exit on_pgdata_storage_detaching: primary cannot be retrieved")
            return

        if self.unit.name != primary:
            return

        if not self._patroni.are_all_members_ready():
            logger.warning(
                "could not switchover because not all members are ready"
                " - an automatic failover will be triggered"
            )
            return

        # Try to switchover to another member and raise an exception if it doesn't succeed.
        # If it doesn't happen on time, Patroni will automatically run a fail-over.
        try:
            # Get the current primary to check if it has changed later.
            current_primary = self._patroni.get_primary()

            # Trigger the switchover.
            self._patroni.switchover()

            # Wait for the switchover to complete.
            self._patroni.primary_changed(current_primary)

            logger.info("successful switchover")
        except (RetryError, SwitchoverFailedError) as e:
            logger.warning(
                f"switchover failed with reason: {e} - an automatic failover will be triggered"
            )
            return

        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        if self.primary_endpoint:
            self._update_relation_endpoints()

    def _on_peer_relation_changed(self, event: HookEvent):
        """Reconfigure cluster members when something changes."""
        # Prevents the cluster to be reconfigured before it's bootstrapped in the leader.
        if "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug("Deferring on_peer_relation_changed: cluster not initialized")
            event.defer()
            return

        # If the unit is the leader, it can reconfigure the cluster.
        if self.unit.is_leader() and not self._reconfigure_cluster(event):
            event.defer()
            return

        if self._update_member_ip():
            return

        # Don't update this member before it's part of the members list.
        if self._unit_ip not in self.members_ips:
            logger.debug("Early exit on_peer_relation_changed: Unit not in the members list")
            return

        # Update the list of the cluster members in the replicas to make them know each other.
        try:
            # Update the members of the cluster in the Patroni configuration on this unit.
            self.update_config()
        except RetryError:
            self.unit.status = BlockedStatus("failed to update cluster members on member")
            return
        except ValueError as e:
            self.unit.status = BlockedStatus("Configuration Error. Please check the logs")
            logger.error("Invalid configuration: %s", str(e))
            return

        # Start can be called here multiple times as it's idempotent.
        # At this moment, it starts Patroni at the first time the data is received
        # in the relation.
        self._patroni.start_patroni()

        # Assert the member is up and running before marking the unit as active.
        if not self._patroni.member_started:
            logger.debug("Deferring on_peer_relation_changed: awaiting for member to start")
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        # Restart the workload if it's stuck on the starting state after a timeline divergence
        # due to a backup that was restored.
        if not self.is_primary and (
            self._patroni.member_replication_lag == "unknown"
            or int(self._patroni.member_replication_lag) > 1000
        ):
            self._patroni.reinitialize_postgresql()
            logger.debug("Deferring on_peer_relation_changed: reinitialising replica")
            self.unit.status = MaintenanceStatus("reinitialising replica")
            event.defer()
            return

        self._start_stop_pgbackrest_service(event)

        self._update_new_unit_status()

    # Split off into separate function, because of complexity _on_peer_relation_changed
    def _start_stop_pgbackrest_service(self, event: HookEvent) -> None:
        # Start or stop the pgBackRest TLS server service when TLS certificate change.
        if not self.backup.start_stop_pgbackrest_service():
            logger.debug(
                "Deferring on_peer_relation_changed: awaiting for TLS server service to start on primary"
            )
            event.defer()
            return

        self.backup.coordinate_stanza_fields()

        self.backup.check_stanza()

        if "exporter-started" not in self.unit_peer_data:
            self._setup_exporter()

    def _update_new_unit_status(self) -> None:
        """Update the status of a new unit that recently joined the cluster."""
        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        if self.primary_endpoint:
            self._update_relation_endpoints()
            if not self.is_blocked:
                self.unit.status = ActiveStatus()
        else:
            self.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

    def _reconfigure_cluster(self, event: HookEvent):
        """Reconfigure the cluster by adding and removing members IPs to it.

        Returns:
            Whether it was possible to reconfigure the cluster.
        """
        if (
            hasattr(event, "unit")
            and event.relation.data.get(event.unit) is not None
            and event.relation.data[event.unit].get("ip-to-remove") is not None
        ):
            ip_to_remove = event.relation.data[event.unit].get("ip-to-remove")
            logger.info("Removing %s from the cluster due to IP change", ip_to_remove)
            try:
                self._patroni.remove_raft_member(ip_to_remove)
            except RemoveRaftMemberFailedError:
                logger.debug("Deferring on_peer_relation_changed: failed to remove raft member")
                return False
            if ip_to_remove in self.members_ips:
                self._remove_from_members_ips(ip_to_remove)
        self._add_members(event)
        return True

    def _update_member_ip(self) -> bool:
        """Update the member IP in the unit databag.

        Returns:
            Whether the IP was updated.
        """
        # Stop Patroni (and update the member IP) if it was previously isolated
        # from the cluster network. Patroni will start back when its IP address is
        # updated in all the units through the peer relation changed event (in that
        # hook, the configuration is updated and the service is started - or only
        # reloaded in the other units).
        stored_ip = self.unit_peer_data.get("ip")
        current_ip = self.get_hostname_by_unit(None)
        if stored_ip is None:
            self.unit_peer_data.update({"ip": current_ip})
            return False
        elif current_ip != stored_ip:
            logger.info(f"ip changed from {stored_ip} to {current_ip}")
            self.unit_peer_data.update({"ip-to-remove": stored_ip})
            self.unit_peer_data.update({"ip": current_ip})
            self._patroni.stop_patroni()
            self._update_certificate()
            return True
        else:
            self.unit_peer_data.update({"ip-to-remove": ""})
            return False

    def _add_members(self, event):
        """Add new cluster members.

        This method is responsible for adding new members to the cluster
        when new units are added to the application. This event is deferred if
        one of the current units is copying data from the primary, to avoid
        multiple units copying data at the same time, which can cause slow
        transfer rates in these processes and overload the primary instance.
        """
        try:
            # Compare set of Patroni cluster members and Juju hosts
            # to avoid the unnecessary reconfiguration.
            if self._patroni.cluster_members == self._hosts:
                logger.debug("Early exit add_members: Patroni members equal Juju hosts")
                return

            logger.info("Reconfiguring cluster")
            self.unit.status = MaintenanceStatus("reconfiguring cluster")
            for member in self._hosts - self._patroni.cluster_members:
                logger.debug("Adding %s to cluster", member)
                self.add_cluster_member(member)
            self._patroni.update_synchronous_node_count()
        except NotReadyError:
            logger.info("Deferring reconfigure: another member doing sync right now")
            event.defer()
        except RetryError:
            logger.info("Deferring reconfigure: couldn't retrieve current cluster members")
            event.defer()

    def add_cluster_member(self, member: str) -> None:
        """Add member to the cluster if all members are already up and running.

        Raises:
            NotReadyError if either the new member or the current members are not ready.
        """
        unit = self.model.get_unit("/".join(member.rsplit("-", 1)))
        member_ip = self._get_unit_ip(unit)

        if not self._patroni.are_all_members_ready():
            logger.info("not all members are ready")
            raise NotReadyError("not all members are ready")

        # Add the member to the list that should be updated in each other member.
        self._add_to_members_ips(member_ip)

        # Update Patroni configuration file.
        try:
            self.update_config()
        except RetryError:
            self.unit.status = BlockedStatus("failed to update cluster members on member")

    def _get_unit_ip(self, unit: Unit) -> Optional[str]:
        """Get the IP address of a specific unit."""
        # Check if host is current host.
        if unit == self.unit:
            return str(self.model.get_binding(PEER).network.bind_address)
        # Check if host is a peer.
        elif unit in self._peers.data:
            return str(self._peers.data[unit].get("private-address"))
        # Return None if the unit is not a peer neither the current unit.
        else:
            return None

    @property
    def _hosts(self) -> set:
        """List of the current Juju hosts.

        Returns:
            a set containing the current Juju hosts
                with the names using - instead of /
                to match Patroni members names
        """
        peers = self.model.get_relation(PEER)
        hosts = [self.unit.name.replace("/", "-")] + [
            unit.name.replace("/", "-") for unit in peers.units
        ]
        return set(hosts)

    @property
    def _patroni(self) -> Patroni:
        """Returns an instance of the Patroni object."""
        return Patroni(
            self._unit_ip,
            self.cluster_name,
            self._member_name,
            self.app.planned_units(),
            self._peer_members_ips,
            self._get_password(),
            self._replication_password,
            self.get_secret(APP_SCOPE, REWIND_PASSWORD_KEY),
            bool(self.unit_peer_data.get("tls")),
        )

    @property
    def is_primary(self) -> bool:
        """Return whether this unit is the primary instance."""
        return self.unit.name == self._patroni.get_primary(unit_name_pattern=True)

    @property
    def is_tls_enabled(self) -> bool:
        """Return whether TLS is enabled."""
        return all(self.tls.get_tls_files())

    @property
    def _peer_members_ips(self) -> Set[str]:
        """Fetch current list of peer members IPs.

        Returns:
            A list of peer members addresses (strings).
        """
        # Get all members IPs and remove the current unit IP from the list.
        addresses = self.members_ips
        current_unit_ip = self._unit_ip
        if current_unit_ip in addresses:
            addresses.remove(current_unit_ip)
        return addresses

    @property
    def _units_ips(self) -> Set[str]:
        """Fetch current list of peers IPs.

        Returns:
            A list of peers addresses (strings).
        """
        # Get all members IPs and remove the current unit IP from the list.
        addresses = {self._get_unit_ip(unit) for unit in self._peers.units}
        addresses.add(self._unit_ip)
        return addresses

    @property
    def members_ips(self) -> Set[str]:
        """Returns the list of IPs addresses of the current members of the cluster."""
        return set(json.loads(self._peers.data[self.app].get("members_ips", "[]")))

    def _add_to_members_ips(self, ip: str) -> None:
        """Add one IP to the members list."""
        self._update_members_ips(ip_to_add=ip)

    def _remove_from_members_ips(self, ip: str) -> None:
        """Remove IPs from the members list."""
        self._update_members_ips(ip_to_remove=ip)

    def _update_members_ips(self, ip_to_add: str = None, ip_to_remove: str = None) -> None:
        """Update cluster member IPs on application data.

        Member IPs on application data are used to determine when a unit of PostgreSQL
        should be added or removed from the PostgreSQL cluster.

        NOTE: this function does not update the IPs on the PostgreSQL cluster
        in the Patroni configuration.
        """
        # Allow leader to reset which members are part of the cluster.
        if not self.unit.is_leader():
            return

        ips = json.loads(self._peers.data[self.app].get("members_ips", "[]"))
        if ip_to_add and ip_to_add not in ips:
            ips.append(ip_to_add)
        elif ip_to_remove:
            ips.remove(ip_to_remove)
        self._peers.data[self.app]["members_ips"] = json.dumps(ips)

    @retry(
        stop=stop_after_delay(60),
        wait=wait_fixed(5),
        reraise=True,
    )
    def _change_primary(self) -> None:
        """Change the primary member of the cluster."""
        # Try to switchover to another member and raise an exception if it doesn't succeed.
        # If it doesn't happen on time, Patroni will automatically run a fail-over.
        try:
            # Get the current primary to check if it has changed later.
            current_primary = self._patroni.get_primary()

            # Trigger the switchover.
            self._patroni.switchover()

            # Wait for the switchover to complete.
            self._patroni.primary_changed(current_primary)

            logger.info("successful switchover")
        except (RetryError, SwitchoverFailedError) as e:
            logger.warning(
                f"switchover failed with reason: {e} - an automatic failover will be triggered"
            )

    @property
    def _unit_ip(self) -> str:
        """Current unit ip."""
        return str(self.model.get_binding(PEER).network.bind_address)

    def _on_cluster_topology_change(self, _):
        """Updates endpoints and (optionally) certificates when the cluster topology changes."""
        logger.info("Cluster topology changed")
        if self.primary_endpoint:
            self._update_relation_endpoints()
            self.unit.status = ActiveStatus()

    def _on_install(self, event: InstallEvent) -> None:
        """Install prerequisites for the application."""
        if not self._is_storage_attached():
            self._reboot_on_detached_storage(event)
            return

        self.unit.status = MaintenanceStatus("installing PostgreSQL")

        # Install the charmed PostgreSQL snap.
        try:
            self._install_snap_packages(packages=SNAP_PACKAGES)
        except snap.SnapError:
            self.unit.status = BlockedStatus("failed to install snap packages")
            return

        cache = snap.SnapCache()
        postgres_snap = cache[POSTGRESQL_SNAP_NAME]
        postgres_snap.alias("patronictl")
        postgres_snap.alias("psql")

        # Create the user home directory for the snap_daemon user.
        # This is needed due to https://bugs.launchpad.net/snapd/+bug/2011581.
        try:
            subprocess.check_call("mkdir -p /home/snap_daemon".split())
            subprocess.check_call("chown snap_daemon:snap_daemon /home/snap_daemon".split())
            subprocess.check_call("usermod -d /home/snap_daemon snap_daemon".split())
        except subprocess.CalledProcessError:
            logger.exception("Unable to create snap_daemon home dir")

        self.unit.status = WaitingStatus("waiting to start PostgreSQL")

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle the leader-elected event."""
        # The leader sets the needed passwords if they weren't set before.
        if self.get_secret(APP_SCOPE, USER_PASSWORD_KEY) is None:
            self.set_secret(APP_SCOPE, USER_PASSWORD_KEY, new_password())
        if self.get_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY) is None:
            self.set_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY, new_password())
        if self.get_secret(APP_SCOPE, REWIND_PASSWORD_KEY) is None:
            self.set_secret(APP_SCOPE, REWIND_PASSWORD_KEY, new_password())
        if self.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY) is None:
            self.set_secret(APP_SCOPE, MONITORING_PASSWORD_KEY, new_password())

        # Update the list of the current PostgreSQL hosts when a new leader is elected.
        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._unit_ip not in self.members_ips:
            self._add_to_members_ips(self._unit_ip)

        # Remove departing units when the leader changes.
        for ip in self._get_ips_to_remove():
            logger.info("Removing %s from the cluster", ip)
            self._remove_from_members_ips(ip)

        self.update_config()

        # Don't update connection endpoints in the first time this event run for
        # this application because there are no primary and replicas yet.
        if "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug("Early exit on_leader_elected: Cluster not initialized")
            return

        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        if self.primary_endpoint:
            self._update_relation_endpoints()
        else:
            self.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

    def _on_config_changed(self, event) -> None:
        """Handle configuration changes, like enabling plugins."""
        if not self.is_cluster_initialised:
            logger.debug("Defer on_config_changed: cluster not initialised yet")
            event.defer()
            return

        if not self.upgrade.idle:
            logger.debug("Defer on_config_changed: upgrade in progress")
            event.defer()
            return
        try:
            self._validate_config_options()
            # update config on every run
            self.update_config()
        except psycopg2.OperationalError:
            logger.debug("Defer on_config_changed: Cannot connect to database")
            event.defer()
            return
        except ValueError as e:
            self.unit.status = BlockedStatus("Configuration Error. Please check the logs")
            logger.error("Invalid configuration: %s", str(e))
            return

        if self.is_blocked and "Configuration Error" in self.unit.status.message:
            self.unit.status = ActiveStatus()

        if not self.unit.is_leader():
            return

        # Enable and/or disable the extensions.
        self.enable_disable_extensions()

        # Unblock the charm after extensions are enabled (only if it's blocked due to application
        # charms requesting extensions).
        if self.unit.status.message != EXTENSIONS_BLOCKING_MESSAGE:
            return

        for relation in [
            *self.model.relations.get("db", []),
            *self.model.relations.get("db-admin", []),
        ]:
            if not self.legacy_db_relation.set_up_relation(relation):
                logger.debug(
                    "Early exit on_config_changed: legacy relation requested extensions that are still disabled"
                )
                return

    def enable_disable_extensions(self, database: str = None) -> None:
        """Enable/disable PostgreSQL extensions set through config options.

        Args:
            database: optional database where to enable/disable the extension.
        """
        spi_module = ["refint", "autoinc", "insert_username", "moddatetime"]
        plugins_exception = {"uuid_ossp": '"uuid-ossp"'}
        original_status = self.unit.status
        extensions = {}
        # collect extensions
        for plugin in self.config.plugin_keys():
            enable = self.config[plugin]

            # Enable or disable the plugin/extension.
            extension = "_".join(plugin.split("_")[1:-1])
            if extension == "spi":
                for ext in spi_module:
                    extensions[ext] = enable
                continue
            extension = plugins_exception.get(extension, extension)
            if self._check_extension_dependencies(extension, enable):
                self.unit.status = BlockedStatus(EXTENSIONS_DEPENDENCY_MESSAGE)
                return
            extensions[extension] = enable
        if self.is_blocked and self.unit.status.message == EXTENSIONS_DEPENDENCY_MESSAGE:
            self.unit.status = ActiveStatus()
        self.unit.status = WaitingStatus("Updating extensions")
        try:
            self.postgresql.enable_disable_extensions(extensions, database)
        except PostgreSQLEnableDisableExtensionError as e:
            logger.exception("failed to change plugins: %s", str(e))
        self.unit.status = original_status

    def _check_extension_dependencies(self, extension: str, enable: bool) -> bool:
        skip = False
        if enable and extension in REQUIRED_PLUGINS:
            for ext in REQUIRED_PLUGINS[extension]:
                if not self.config[f"plugin_{ext}_enable"]:
                    skip = True
                    logger.exception(
                        "cannot enable %s, extension required %s to be enabled before",
                        extension,
                        ext,
                    )
        return skip

    def _get_ips_to_remove(self) -> Set[str]:
        """List the IPs that were part of the cluster but departed."""
        old = self.members_ips
        current = self._units_ips
        return old - current

    def _can_start(self, event: StartEvent) -> bool:
        """Returns whether the workload can be started on this unit."""
        if not self._is_storage_attached():
            self._reboot_on_detached_storage(event)
            return False

        # Safeguard against starting while upgrading.
        if not self.upgrade.idle:
            logger.debug("Defer on_start: Cluster is upgrading")
            event.defer()
            return False

        # Doesn't try to bootstrap the cluster if it's in a blocked state
        # caused, for example, because a failed installation of packages.
        if self.is_blocked:
            logger.debug("Early exit on_start: Unit blocked")
            return False

        return True

    def _on_start(self, event: StartEvent) -> None:
        """Handle the start event."""
        if not self._can_start(event):
            return

        postgres_password = self._get_password()
        # If the leader was not elected (and the needed passwords were not generated yet),
        # the cluster cannot be bootstrapped yet.
        if not postgres_password or not self._replication_password:
            logger.info("leader not elected and/or passwords not yet generated")
            self.unit.status = WaitingStatus("awaiting passwords generation")
            event.defer()
            return

        self.unit_peer_data.update({"ip": self.get_hostname_by_unit(None)})

        self.unit.set_workload_version(self._patroni.get_postgresql_version())

        # Open port
        try:
            self.unit.open_port("tcp", 5432)
        except ModelError:
            logger.exception("failed to open port")

        # Only the leader can bootstrap the cluster.
        # On replicas, only prepare for starting the instance later.
        if not self.unit.is_leader():
            self._start_replica(event)
            return

        # Bootstrap the cluster in the leader unit.
        self._start_primary(event)

    def _setup_exporter(self) -> None:
        """Set up postgresql_exporter options."""
        cache = snap.SnapCache()
        postgres_snap = cache[POSTGRESQL_SNAP_NAME]

        if postgres_snap.revision != list(
            filter(lambda snap_package: snap_package[0] == POSTGRESQL_SNAP_NAME, SNAP_PACKAGES)
        )[0][1]["revision"].get(platform.machine()):
            logger.debug(
                "Early exit _setup_exporter: snap was not refreshed to the right version yet"
            )
            return

        postgres_snap.set({
            "exporter.user": MONITORING_USER,
            "exporter.password": self.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
        })
        if postgres_snap.services[MONITORING_SNAP_SERVICE]["active"] is False:
            postgres_snap.start(services=[MONITORING_SNAP_SERVICE], enable=True)
        else:
            postgres_snap.restart(services=[MONITORING_SNAP_SERVICE])
        self.unit_peer_data.update({"exporter-started": "True"})

    def _start_primary(self, event: StartEvent) -> None:
        """Bootstrap the cluster."""
        # Set some information needed by Patroni to bootstrap the cluster.
        if not self._patroni.bootstrap_cluster():
            self.unit.status = BlockedStatus("failed to start Patroni")
            return

        # Assert the member is up and running before marking it as initialised.
        if not self._patroni.member_started:
            logger.debug("Deferring on_start: awaiting for member to start")
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        # Create the default postgres database user that is needed for some
        # applications (not charms) like Landscape Server.
        try:
            # This event can be run on a replica if the machines are restarted.
            # For that case, check whether the postgres user already exits.
            users = self.postgresql.list_users()
            if "postgres" not in users:
                self.postgresql.create_user("postgres", new_password(), admin=True)
                # Create the backup user.
            if BACKUP_USER not in users:
                self.postgresql.create_user(BACKUP_USER, new_password(), admin=True)
            if MONITORING_USER not in users:
                # Create the monitoring user.
                self.postgresql.create_user(
                    MONITORING_USER,
                    self.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
                    extra_user_roles="pg_monitor",
                )
        except PostgreSQLCreateUserError as e:
            logger.exception(e)
            self.unit.status = BlockedStatus("Failed to create postgres user")
            return
        except PostgreSQLListUsersError:
            logger.warning("Deferriing on_start: Unable to list users")
            event.defer()
            return

        self.postgresql.set_up_database()

        self.postgresql_client_relation.oversee_users()

        # Set the flag to enable the replicas to start the Patroni service.
        self._peers.data[self.app]["cluster_initialised"] = "True"

        # Clear unit data if this unit became a replica after a failover/switchover.
        self._update_relation_endpoints()

        # Enable/disable PostgreSQL extensions if they were set before the cluster
        # was fully initialised.
        self.enable_disable_extensions()

        self.unit.status = ActiveStatus()

    def _start_replica(self, event) -> None:
        """Configure the replica if the cluster was already initialised."""
        if "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug("Deferring on_start: awaiting for cluster to start")
            self.unit.status = WaitingStatus("awaiting for cluster to start")
            event.defer()
            return

        # Member already started, so we can set an ActiveStatus.
        # This can happen after a reboot.
        if self._patroni.member_started:
            self.unit.status = ActiveStatus()
            return

        # Configure Patroni in the replica but don't start it yet.
        self._patroni.configure_patroni_on_unit()

    def _on_get_password(self, event: ActionEvent) -> None:
        """Returns the password for a user as an action response.

        If no user is provided, the password of the operator user is returned.
        """
        username = event.params.get("username", USER)
        if username not in SYSTEM_USERS:
            event.fail(
                f"The action can be run only for users used by the charm or Patroni:"
                f" {', '.join(SYSTEM_USERS)} not {username}"
            )
            return
        event.set_results({"password": self.get_secret(APP_SCOPE, f"{username}-password")})

    def _on_set_password(self, event: ActionEvent) -> None:
        """Set the password for the specified user."""
        # Only leader can write the new password into peer relation.
        if not self.unit.is_leader():
            event.fail("The action can be run only on leader unit")
            return

        username = event.params.get("username", USER)
        if username not in SYSTEM_USERS:
            event.fail(
                f"The action can be run only for users used by the charm:"
                f" {', '.join(SYSTEM_USERS)} not {username}"
            )
            return

        password = event.params.get("password", new_password())

        if password == self.get_secret(APP_SCOPE, f"{username}-password"):
            event.log("The old and new passwords are equal.")
            event.set_results({"password": password})
            return

        # Ensure all members are ready before trying to reload Patroni
        # configuration to avoid errors (like the API not responding in
        # one instance because PostgreSQL and/or Patroni are not ready).
        if not self._patroni.are_all_members_ready():
            event.fail(
                "Failed changing the password: Not all members healthy or finished initial sync."
            )
            return

        # Update the password in the PostgreSQL instance.
        try:
            self.postgresql.update_user_password(username, password)
        except PostgreSQLUpdateUserPasswordError as e:
            logger.exception(e)
            event.fail(
                "Failed changing the password: Not all members healthy or finished initial sync."
            )
            return

        # Update the password in the secret store.
        self.set_secret(APP_SCOPE, f"{username}-password", password)

        # Update and reload Patroni configuration in this unit to use the new password.
        # Other units Patroni configuration will be reloaded in the peer relation changed event.
        self.update_config()

        event.set_results({"password": password})

    def _on_update_status(self, _) -> None:
        """Update the unit status message and users list in the database."""
        if not self._can_run_on_update_status():
            return

        if "restoring-backup" in self.app_peer_data:
            if "failed" in self._patroni.get_member_status(self._member_name):
                logger.error("Restore failed: database service failed to start")
                self.unit.status = BlockedStatus("Failed to restore backup")
                return

            if not self._patroni.member_started:
                logger.debug("on_update_status early exit: Patroni has not started yet")
                return

            # Remove the restoring backup flag and the restore stanza name.
            self.app_peer_data.update({"restoring-backup": "", "restore-stanza": ""})
            self.update_config()
            logger.info("Restore succeeded")

            can_use_s3_repository, validation_message = self.backup.can_use_s3_repository()
            if not can_use_s3_repository:
                self.unit.status = BlockedStatus(validation_message)
                return

        if self._handle_processes_failures():
            return

        self.postgresql_client_relation.oversee_users()
        if self.primary_endpoint:
            self._update_relation_endpoints()

        if self._handle_workload_failures():
            return

        self._set_primary_status_message()

        # Restart topology observer if it is gone
        self._observer.start_observer()

    def _can_run_on_update_status(self) -> bool:
        if "cluster_initialised" not in self._peers.data[self.app]:
            return False

        if not self.upgrade.idle:
            logger.debug("Early exit on_update_status: upgrade in progress")
            return False

        if self.is_blocked:
            logger.debug("on_update_status early exit: Unit is in Blocked status")
            return False

        return True

    def _handle_processes_failures(self) -> bool:
        """Handle Patroni and PostgreSQL OS processes failures.

        Returns:
            a bool indicating whether the charm performed any action.
        """
        # Restart the PostgreSQL process if it was frozen (in that case, the Patroni
        # process is running by the PostgreSQL process not).
        if self._unit_ip in self.members_ips and self._patroni.member_inactive:
            try:
                self._patroni.restart_patroni()
                logger.info("restarted PostgreSQL because it was not running")
                return True
            except RetryError:
                logger.error("failed to restart PostgreSQL after checking that it was not running")
                return False

        return False

    def _handle_workload_failures(self) -> bool:
        """Handle workload (Patroni or PostgreSQL) failures.

        Returns:
            a bool indicating whether the charm performed any action.
        """
        # Restart the workload if it's stuck on the starting state after a restart.
        if (
            not self._patroni.member_started
            and "postgresql_restarted" in self._peers.data[self.unit]
            and self._patroni.member_replication_lag == "unknown"
        ):
            self._patroni.reinitialize_postgresql()
            return True

        # Restart the service if the current cluster member is isolated from the cluster
        # (stuck with the "awaiting for member to start" message).
        if not self._patroni.member_started and self._patroni.is_member_isolated:
            self._patroni.restart_patroni()
            return True

        return False

    def _set_primary_status_message(self) -> None:
        """Display 'Primary' in the unit status message if the current unit is the primary."""
        try:
            if self._patroni.get_primary(unit_name_pattern=True) == self.unit.name:
                self.unit.status = ActiveStatus("Primary")
            elif self._patroni.member_started:
                self.unit.status = ActiveStatus()
        except (RetryError, ConnectionError) as e:
            logger.error(f"failed to get primary with error {e}")

    def _update_certificate(self) -> None:
        """Updates the TLS certificate if the unit IP changes."""
        # Request the certificate only if there is already one. If there isn't,
        # the certificate will be generated in the relation joined event when
        # relating to the TLS Certificates Operator.
        if all(self.tls.get_tls_files()):
            self.tls._request_certificate(self.get_secret("unit", "private-key"))

    @property
    def is_blocked(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    def _get_password(self) -> str:
        """Get operator user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret(APP_SCOPE, USER_PASSWORD_KEY)

    @property
    def _replication_password(self) -> str:
        """Get replication user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY)

    def _install_snap_packages(self, packages: List[str], refresh: bool = False) -> None:
        """Installs package(s) to container.

        Args:
            packages: list of packages to install.
            refresh: whether to refresh the snap if it's
                already present.
        """
        for snap_name, snap_version in packages:
            try:
                snap_cache = snap.SnapCache()
                snap_package = snap_cache[snap_name]

                if not snap_package.present or refresh:
                    if revision := snap_version.get("revision"):
                        try:
                            revision = revision[platform.machine()]
                        except Exception:
                            logger.error("Unavailable snap architecture %s", platform.machine())
                            raise
                        channel = snap_version.get("channel", "")
                        snap_package.ensure(
                            snap.SnapState.Latest, revision=revision, channel=channel
                        )
                        snap_package.hold()
                    else:
                        snap_package.ensure(snap.SnapState.Latest, channel=snap_version["channel"])

            except (snap.SnapError, snap.SnapNotFoundError) as e:
                logger.error(
                    "An exception occurred when installing %s. Reason: %s", snap_name, str(e)
                )
                raise

    def _is_storage_attached(self) -> bool:
        """Returns if storage is attached."""
        try:
            subprocess.check_call(["mountpoint", "-q", self._storage_path])
            return True
        except subprocess.CalledProcessError:
            return False

    @property
    def _peers(self) -> Relation:
        """Fetch the peer relation.

        Returns:
             A:class:`ops.model.Relation` object representing
             the peer relation.
        """
        return self.model.get_relation(PEER)

    def push_tls_files_to_workload(self) -> bool:
        """Move TLS files to the PostgreSQL storage path and enable TLS."""
        key, ca, cert = self.tls.get_tls_files()
        if key is not None:
            self._patroni.render_file(f"{PATRONI_CONF_PATH}/{TLS_KEY_FILE}", key, 0o600)
        if ca is not None:
            self._patroni.render_file(f"{PATRONI_CONF_PATH}/{TLS_CA_FILE}", ca, 0o600)
        if cert is not None:
            self._patroni.render_file(f"{PATRONI_CONF_PATH}/{TLS_CERT_FILE}", cert, 0o600)

        return self.update_config()

    def _reboot_on_detached_storage(self, event: EventBase) -> None:
        """Reboot on detached storage.

        Workaround for lxd containers not getting storage attached on startups.

        Args:
            event: the event that triggered this handler
        """
        event.defer()
        logger.error("Data directory not attached. Reboot unit.")
        self.unit.status = WaitingStatus("Data directory not attached")
        try:
            subprocess.check_call(["systemctl", "reboot"])
        except subprocess.CalledProcessError:
            pass

    def _restart(self, event: RunWithLock) -> None:
        """Restart PostgreSQL."""
        if not self._patroni.are_all_members_ready():
            logger.debug("Early exit _restart: not all members ready yet")
            event.defer()
            return

        try:
            self._patroni.restart_postgresql()
            self._peers.data[self.unit]["postgresql_restarted"] = "True"
        except RetryError:
            error_message = "failed to restart PostgreSQL"
            logger.exception(error_message)
            self.unit.status = BlockedStatus(error_message)
            return

        # Start or stop the pgBackRest TLS server service when TLS certificate change.
        self.backup.start_stop_pgbackrest_service()

    @property
    def _is_workload_running(self) -> bool:
        """Returns whether the workload is running (in an active state)."""
        snap_cache = snap.SnapCache()
        charmed_postgresql_snap = snap_cache["charmed-postgresql"]
        if not charmed_postgresql_snap.present:
            return False

        return charmed_postgresql_snap.services["patroni"]["active"]

    @property
    def _can_connect_to_postgresql(self) -> bool:
        try:
            for attempt in Retrying(stop=stop_after_delay(30), wait=wait_fixed(3)):
                with attempt:
                    assert self.postgresql.get_postgresql_timezones()
        except RetryError:
            logger.debug("Cannot connect to database")
            return False
        return True

    def update_config(self, is_creating_backup: bool = False) -> bool:
        """Updates Patroni config file based on the existence of the TLS files."""
        if (
            self.model.config.get("profile-limit-memory") is not None
            and self.model.config.get("profile_limit_memory") is not None
        ):
            raise ValueError(
                "Both profile-limit-memory and profile_limit_memory are set. Please use only one of them."
            )

        enable_tls = self.is_tls_enabled
        limit_memory = None
        if self.config.profile_limit_memory:
            limit_memory = self.config.profile_limit_memory * 10**6

        # Build PostgreSQL parameters.
        pg_parameters = self.postgresql.build_postgresql_parameters(
            self.model.config, self.get_available_memory(), limit_memory
        )

        # Update and reload configuration based on TLS files availability.
        self._patroni.render_patroni_yml_file(
            connectivity=self.unit_peer_data.get("connectivity", "on") == "on",
            is_creating_backup=is_creating_backup,
            enable_tls=enable_tls,
            backup_id=self.app_peer_data.get("restoring-backup"),
            stanza=self.app_peer_data.get("stanza"),
            restore_stanza=self.app_peer_data.get("restore-stanza"),
            parameters=pg_parameters,
        )
        if not self._is_workload_running:
            # If Patroni/PostgreSQL has not started yet and TLS relations was initialised,
            # then mark TLS as enabled. This commonly happens when the charm is deployed
            # in a bundle together with the TLS certificates operator. This flag is used to
            # know when to call the Patroni API using HTTP or HTTPS.
            self.unit_peer_data.update({"tls": "enabled" if enable_tls else ""})
            logger.debug("Early exit update_config: Workload not started yet")
            return True

        if not self._patroni.member_started:
            logger.debug("Early exit update_config: Patroni not started yet")
            return False

        # Try to connect
        if not self._can_connect_to_postgresql:
            logger.warning("Early exit update_config: Cannot connect to Postgresql")
            return False

        self._patroni.bulk_update_parameters_controller_by_patroni({
            "max_connections": max(4 * os.cpu_count(), 100),
            "max_prepared_transactions": self.config.memory_max_prepared_transactions,
        })

        self._handle_postgresql_restart_need(enable_tls)

        # Restart the monitoring service if the password was rotated
        cache = snap.SnapCache()
        postgres_snap = cache[POSTGRESQL_SNAP_NAME]

        try:
            snap_password = postgres_snap.get("exporter.password")
        except snap.SnapError:
            logger.warning(
                "Early exit update_config: Trying to reset metrics service with no configuration set"
            )
            return True
        if snap_password != self.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY):
            self._setup_exporter()

        return True

    def _validate_config_options(self) -> None:
        """Validates specific config options that need access to the database or to the TLS status."""
        if (
            self.config.instance_default_text_search_config
            not in self.postgresql.get_postgresql_text_search_configs()
        ):
            raise ValueError(
                "instance_default_text_search_config config option has an invalid value"
            )

        if not self.postgresql.validate_date_style(self.config.request_date_style):
            raise ValueError("request_date_style config option has an invalid value")

        if self.config.request_time_zone not in self.postgresql.get_postgresql_timezones():
            raise ValueError("request_time_zone config option has an invalid value")

    def _handle_postgresql_restart_need(self, enable_tls: bool) -> None:
        """Handle PostgreSQL restart need based on the TLS configuration and configuration changes."""
        restart_postgresql = self.is_tls_enabled != self.postgresql.is_tls_enabled()
        self._patroni.reload_patroni_configuration()
        # Wait for some more time than the Patroni's loop_wait default value (10 seconds),
        # which tells how much time Patroni will wait before checking the configuration
        # file again to reload it.
        try:
            for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
                with attempt:
                    restart_postgresql = restart_postgresql or self.postgresql.is_restart_pending()
                    if not restart_postgresql:
                        raise Exception
        except RetryError:
            # Ignore the error, as it happens only to indicate that the configuration has not changed.
            pass
        self.unit_peer_data.update({"tls": "enabled" if enable_tls else ""})

        # Restart PostgreSQL if TLS configuration has changed
        # (so the both old and new connections use the configuration).
        if restart_postgresql:
            logger.info("PostgreSQL restart required")
            self._peers.data[self.unit].pop("postgresql_restarted", None)
            self.on[self.restart_manager.name].acquire_lock.emit()

    def _update_relation_endpoints(self) -> None:
        """Updates endpoints and read-only endpoint in all relations."""
        self.postgresql_client_relation.update_endpoints()
        self.legacy_db_relation.update_endpoints()
        self.legacy_db_admin_relation.update_endpoints()

    def get_available_memory(self) -> int:
        """Returns the system available memory in bytes."""
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if "MemTotal" in line:
                    return int(line.split()[1]) * 1024

        return 0

    @property
    def client_relations(self) -> List[Relation]:
        """Return the list of established client relations."""
        relations = []
        for relation_name in ["database", "db", "db-admin"]:
            for relation in self.model.relations.get(relation_name, []):
                relations.append(relation)
        return relations


if __name__ == "__main__":
    main(PostgresqlOperatorCharm)
