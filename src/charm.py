#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for the PostgreSQL database."""
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Set

from charms.operator_libs_linux.v0 import apt
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQL,
    PostgreSQLCreateUserError,
    PostgreSQLUpdateUserPasswordError,
)
from charms.postgresql_k8s.v0.postgresql_tls import PostgreSQLTLS
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops.charm import (
    ActionEvent,
    CharmBase,
    ConfigChangedEvent,
    InstallEvent,
    LeaderElectedEvent,
    RelationChangedEvent,
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
from tenacity import RetryError, Retrying, retry, stop_after_delay, wait_fixed

from backups import PostgreSQLBackups
from cluster import (
    AddRaftMemberFailedError,
    NotReadyError,
    Patroni,
    RemoveRaftMemberFailedError,
    SwitchoverFailedError,
)
from cluster_topology_observer import (
    ClusterTopologyChangeCharmEvents,
    ClusterTopologyObserver,
)
from constants import (
    BACKUP_USER,
    PEER,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    SYSTEM_USERS,
    TLS_CA_FILE,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    USER,
    USER_PASSWORD_KEY,
)
from relations.db import DbProvides
from relations.postgresql_provider import PostgreSQLProvider
from utils import new_password

logger = logging.getLogger(__name__)

NO_PRIMARY_MESSAGE = "no primary in the cluster"
CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    on = ClusterTopologyChangeCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"

        self._observer = ClusterTopologyObserver(self)
        self.framework.observe(self.on.cluster_topology_change, self._on_cluster_topology_change)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on[PEER].relation_departed, self._on_peer_relation_departed)
        self.framework.observe(self.on.pgdata_storage_detaching, self._on_pgdata_storage_detaching)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.get_password_action, self._on_get_password)
        self.framework.observe(self.on.set_password_action, self._on_set_password)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.cluster_name = self.app.name
        self._member_name = self.unit.name.replace("/", "-")
        self._storage_path = self.meta.storages["pgdata"].location

        self.postgresql_client_relation = PostgreSQLProvider(self)
        self.legacy_db_relation = DbProvides(self, admin=False)
        self.legacy_db_admin_relation = DbProvides(self, admin=True)
        self.backup = PostgreSQLBackups(self, "s3-parameters")
        self.tls = PostgreSQLTLS(self, PEER)
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )
        self._observer.start_observer()

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

    def get_secret(self, scope: str, key: str) -> Optional[str]:
        """Get secret from the secret storage."""
        if scope == "unit":
            return self.unit_peer_data.get(key, None)
        elif scope == "app":
            return self.app_peer_data.get(key, None)
        else:
            raise RuntimeError("Unknown secret scope.")

    def set_secret(self, scope: str, key: str, value: Optional[str]) -> None:
        """Get secret from the secret storage."""
        if scope == "unit":
            if not value:
                del self.unit_peer_data[key]
                return
            self.unit_peer_data.update({key: value})
        elif scope == "app":
            if not value:
                del self.app_peer_data[key]
                return
            self.app_peer_data.update({key: value})
        else:
            raise RuntimeError("Unknown secret scope.")

    @property
    def postgresql(self) -> PostgreSQL:
        """Returns an instance of the object used to interact with the database."""
        return PostgreSQL(
            primary_host=self.primary_endpoint,
            current_host=self._unit_ip,
            user=USER,
            password=self.get_secret("app", f"{USER}-password"),
            database="postgres",
        )

    @property
    def primary_endpoint(self) -> Optional[str]:
        """Returns the endpoint of the primary instance or None when no primary available."""
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    primary = self._patroni.get_primary()
                    primary_endpoint = self._patroni.get_member_ip(primary)
                    # Force a retry if there is no primary or the member that was
                    # returned is not in the list of the current cluster members
                    # (like when the cluster was not updated yet after a failed switchover).
                    if not primary_endpoint or primary_endpoint not in self._units_ips:
                        raise ValueError()
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
                self.unit.status = BlockedStatus(NO_PRIMARY_MESSAGE)
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

    def _on_peer_relation_changed(self, event: RelationChangedEvent):
        """Reconfigure cluster members when something changes."""
        # Prevents the cluster to be reconfigured before it's bootstrapped in the leader.
        if "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug("Deferring on_peer_relation_changed: cluster not initialized")
            event.defer()
            return

        # If the unit is the leader, it can reconfigure the cluster.
        if self.unit.is_leader():
            self._add_members(event)

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

        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        if self.primary_endpoint:
            self._update_relation_endpoints()
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = BlockedStatus(NO_PRIMARY_MESSAGE)

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
        except NotReadyError:
            logger.info("Deferring reconfigure: another member doing sync right now")
            event.defer()
            return
        except RetryError:
            logger.info("Deferring reconfigure: couldn't retrieve current cluster members")
            event.defer()
            return

        # Try to add the new member to the raft cluster.
        try:
            self._patroni.add_raft_members()
        except AddRaftMemberFailedError:
            # raise NotReadyError("failed to add the new member to the raft cluster")
            event.defer()
            return

        self._patroni.update_synchronous_node_count()

    def add_cluster_member(self, member: str) -> None:
        """Add member to the cluster if all members are already up and running.

        Raises:
            NotReadyError if either the new member or the current members are not ready,
                or if it wasn't possible to add the new member to the raft cluster.
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
            self.app_peer_data.get("archive-mode", "on"),
            self._unit_ip,
            self._storage_path,
            self.cluster_name,
            self._member_name,
            self.app.planned_units(),
            self._peer_members_ips,
            self._get_password(),
            self._replication_password,
            self.get_secret("app", REWIND_PASSWORD_KEY),
            bool(self.unit_peer_data.get("tls")),
        )

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
        self._update_relation_endpoints()
        self._update_certificate()
        if self.is_blocked and self.unit.status.message == NO_PRIMARY_MESSAGE:
            if self.primary_endpoint:
                self.unit.status = ActiveStatus()

    def _on_install(self, event: InstallEvent) -> None:
        """Install prerequisites for the application."""
        if not self._is_storage_attached():
            self._reboot_on_detached_storage(event)
            return

        self.unit.status = MaintenanceStatus("installing PostgreSQL")

        # Prevent the default cluster creation.
        self._inhibit_default_cluster_creation()

        # Install the PostgreSQL and Patroni requirements packages.
        try:
            self._install_apt_packages(
                event, ["pgbackrest", "postgresql", "python3-pip", "python3-psycopg2"]
            )
        except (subprocess.CalledProcessError, apt.PackageNotFoundError):
            self.unit.status = BlockedStatus("failed to install apt packages")
            return

        try:
            resource_path = self.model.resources.fetch("patroni")
        except ModelError as e:
            logger.error(f"missing patroni resource {str(e)}")
            self.unit.status = BlockedStatus("Missing 'patroni' resource")
            return

        try:
            self._install_pip_package("python-dateutil", user="postgres")
        except subprocess.SubprocessError:
            self.unit.status = BlockedStatus("failed to install python-dateutil package")
            return

        # Build Patroni package path with raft dependency and install it.
        try:
            patroni_package_path = f"{str(resource_path)}[raft]"
            self._install_pip_package(patroni_package_path)
        except subprocess.SubprocessError:
            self.unit.status = BlockedStatus("failed to install Patroni python package")
            return

        self.unit.status = WaitingStatus("waiting to start PostgreSQL")

    def _inhibit_default_cluster_creation(self) -> None:
        """Stop the PostgreSQL packages from creating the default cluster."""
        os.makedirs(os.path.dirname(CREATE_CLUSTER_CONF_PATH), mode=0o755, exist_ok=True)
        with open(CREATE_CLUSTER_CONF_PATH, mode="w") as file:
            file.write("create_main_cluster = false\n")

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle the leader-elected event."""
        # The leader sets the needed passwords if they weren't set before.
        if self.get_secret("app", USER_PASSWORD_KEY) is None:
            self.set_secret("app", USER_PASSWORD_KEY, new_password())
        if self.get_secret("app", REPLICATION_PASSWORD_KEY) is None:
            self.set_secret("app", REPLICATION_PASSWORD_KEY, new_password())
        if self.get_secret("app", REWIND_PASSWORD_KEY) is None:
            self.set_secret("app", REWIND_PASSWORD_KEY, new_password())

        # Update the list of the current PostgreSQL hosts when a new leader is elected.
        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._unit_ip not in self.members_ips:
            self._add_to_members_ips(self._unit_ip)

        # Remove departing units when the leader changes.
        for ip in self._get_ips_to_remove():
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
            self.unit.status = BlockedStatus(NO_PRIMARY_MESSAGE)

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Install additional packages through APT."""
        try:
            extra_packages = self.config.get("extra-packages")
            if extra_packages:
                self._install_apt_packages(event, extra_packages.split(" "))
        except (subprocess.CalledProcessError, apt.PackageNotFoundError):
            logger.warning("failed to install apts packages")

        self._update_certificate()

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

        # Only the leader can bootstrap the cluster.
        # On replicas, only prepare for starting the instance later.
        if not self.unit.is_leader():
            self._start_replica(event)
            return

        # Bootstrap the cluster in the leader unit.
        self._start_primary(event)

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
        except PostgreSQLCreateUserError as e:
            logger.exception(e)
            self.unit.status = BlockedStatus("Failed to create postgres user")
            return

        self.postgresql_client_relation.oversee_users()

        # Set the flag to enable the replicas to start the Patroni service.
        self._peers.data[self.app]["cluster_initialised"] = "True"

        # Clear unit data if this unit became a replica after a failover/switchover.
        self._update_relation_endpoints()

        self.unit.status = ActiveStatus()

    def _start_replica(self, event) -> None:
        """Configure the replica if the cluster was already initialised."""
        if "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug("Deferring on_start: awaiting for cluster to start")
            self.unit.status = WaitingStatus("awaiting for cluster to start")
            event.defer()
            return

        # Clear unit data if this unit is still replica.
        self._update_relation_endpoints()

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
        event.set_results({f"{username}-password": self.get_secret("app", f"{username}-password")})

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

        if password == self.get_secret("app", f"{username}-password"):
            event.log("The old and new passwords are equal.")
            event.set_results({f"{username}-password": password})
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
        self.set_secret("app", f"{username}-password", password)

        # Update and reload Patroni configuration in this unit to use the new password.
        # Other units Patroni configuration will be reloaded in the peer relation changed event.
        self.update_config()

        event.set_results({f"{username}-password": password})

    def _on_update_status(self, _) -> None:
        """Update the unit status message and users list in the database."""
        if "cluster_initialised" not in self._peers.data[self.app]:
            return

        if self.is_blocked:
            logger.debug("on_update_status early exit: Unit is in Blocked status")
            return

        self.postgresql_client_relation.oversee_users()
        self._update_relation_endpoints()

        # Restart the workload if it's stuck on the starting state after a restart.
        if (
            not self._patroni.member_started
            and "postgresql_restarted" in self._peers.data[self.unit]
            and self._patroni.member_replication_lag == "unknown"
        ):
            self._patroni.reinitialize_postgresql()
            return

        if "restoring-backup" in self.app_peer_data:
            if "failed" in self._patroni.get_member_status(self._member_name):
                self.unit.status = BlockedStatus("Failed to restore backup")
                return

            if not self._patroni.member_started:
                logger.debug("on_update_status early exit: Patroni has not started yet")
                return

            # Remove the restoring backup flag.
            self.app_peer_data.update({"restoring-backup": ""})
            self.update_config()

        self._set_primary_status_message()

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
        # Update the certificate if the IP changes because the IP
        # is used as the hostname in the certificate subject field.
        if self.get_hostname_by_unit(None) != self.unit_peer_data.get("ip"):
            self.unit_peer_data.update({"ip": self.get_hostname_by_unit(None)})

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
        return self.get_secret("app", USER_PASSWORD_KEY)

    @property
    def _replication_password(self) -> str:
        """Get replication user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        return self.get_secret("app", REPLICATION_PASSWORD_KEY)

    def _install_apt_packages(self, _, packages: List[str]) -> None:
        """Simple wrapper around 'apt-get install -y.

        Raises:
            CalledProcessError if it fails to update the apt cache.
            PackageNotFoundError if the package is not in the cache.
            PackageError if the packages could not be installed.
        """
        try:
            logger.debug("updating apt cache")
            apt.update()
        except subprocess.CalledProcessError as e:
            logger.exception("failed to update apt cache, CalledProcessError", exc_info=e)
            raise

        for package in packages:
            try:
                apt.add_package(package)
                logger.debug(f"installed package: {package}")
            except apt.PackageNotFoundError:
                logger.error(f"package not found: {package}")
                raise
            except apt.PackageError:
                logger.error(f"package error: {package}")
                raise

    def _install_pip_package(self, package: str, user: Optional[str] = None) -> None:
        """Simple wrapper around pip install.

        Raises:
            SubprocessError if the packages could not be installed.
        """
        try:
            command = [
                "pip3",
                "install",
                package,
            ]
            if user:
                command.insert(0, "sudo")
                command.insert(1, "-u")
                command.insert(2, user)
            logger.debug(f"installing python package: {package}")
            subprocess.check_call(command)
        except subprocess.SubprocessError:
            logger.error("could not install pip package")
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

    def push_tls_files_to_workload(self) -> None:
        """Move TLS files to the PostgreSQL storage path and enable TLS."""
        key, ca, cert = self.tls.get_tls_files()
        if key is not None:
            self._patroni.render_file(f"{self._storage_path}/{TLS_KEY_FILE}", key, 0o600)
        if ca is not None:
            self._patroni.render_file(f"{self._storage_path}/{TLS_CA_FILE}", ca, 0o600)
        if cert is not None:
            self._patroni.render_file(f"{self._storage_path}/{TLS_CERT_FILE}", cert, 0o600)

        self.update_config()

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

    def _restart(self, _) -> None:
        """Restart PostgreSQL."""
        try:
            self._patroni.restart_postgresql()
            self._peers.data[self.unit]["postgresql_restarted"] = "True"
        except RetryError as e:
            logger.error("failed to restart PostgreSQL")
            self.unit.status = BlockedStatus(f"failed to restart PostgreSQL with error {e}")

    def update_config(self) -> None:
        """Updates Patroni config file based on the existence of the TLS files."""
        enable_tls = all(self.tls.get_tls_files())

        # Update and reload configuration based on TLS files availability.
        self._patroni.render_patroni_yml_file(
            archive_mode=self.app_peer_data.get("archive-mode", "on"),
            enable_tls=enable_tls,
            backup_id=self.app_peer_data.get("restoring-backup"),
            stanza=self.unit_peer_data.get("stanza"),
        )
        if not self._patroni.member_started:
            # If Patroni/PostgreSQL has not started yet and TLS relations was initialised,
            # then mark TLS as enabled. This commonly happens when the charm is deployed
            # in a bundle together with the TLS certificates operator.
            self.unit_peer_data.update({"tls": "enabled" if enable_tls else ""})
            logger.debug("Early exit update_config: Patroni not started yet")
            return

        restart_postgresql = enable_tls != self.postgresql.is_tls_enabled()
        self._patroni.reload_patroni_configuration()
        self.unit_peer_data.update({"tls": "enabled" if enable_tls else ""})

        # Restart PostgreSQL if TLS configuration has changed
        # (so the both old and new connections use the configuration).
        if restart_postgresql:
            self._peers.data[self.unit].pop("postgresql_restarted", None)
            self.on[self.restart_manager.name].acquire_lock.emit()

    def _update_relation_endpoints(self) -> None:
        """Updates endpoints and read-only endpoint in all relations."""
        self.postgresql_client_relation.update_endpoints()
        self.legacy_db_relation.update_endpoints()
        self.legacy_db_admin_relation.update_endpoints()


if __name__ == "__main__":
    main(PostgresqlOperatorCharm)
