#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for the PostgreSQL database."""
import json
import logging
import os
import subprocess
from typing import List, Optional, Set

from charms.operator_libs_linux.v0 import apt
from charms.postgresql_k8s.v0.postgresql import PostgreSQL
from ops.charm import (
    ActionEvent,
    CharmBase,
    LeaderElectedEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
)
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

from cluster import (
    NotReadyError,
    Patroni,
    RemoveRaftMemberFailedError,
    SwitchoverFailedError,
)
from constants import PEER
from relations.postgresql_provider import PostgreSQLProvider
from utils import new_password

logger = logging.getLogger(__name__)

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on[PEER].relation_departed, self._on_peer_relation_departed)
        self.framework.observe(self.on.pgdata_storage_detaching, self._on_pgdata_storage_detaching)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.get_initial_password_action, self._on_get_initial_password)
        self._cluster_name = self.app.name
        self._member_name = self.unit.name.replace("/", "-")
        self._storage_path = self.meta.storages["pgdata"].location

        self.postgresql_client_relation = PostgreSQLProvider(self)

    @property
    def postgresql(self) -> PostgreSQL:
        """Returns an instance of the object used to interact with the database."""
        return PostgreSQL(
            host=self.primary_endpoint,
            user="postgres",
            password=self._get_postgres_password(),
            database="postgres",
        )

    @property
    def primary_endpoint(self) -> Optional[str]:
        """Returns the endpoint of the primary instance of None if no primary available."""
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    primary = self._patroni.get_primary()
                    primary_endpoint = self._patroni.get_member_ip(primary)
                    # Force a retry if there is no primary or the member that was
                    # returned is not in the list of the current cluster members
                    # (like when the cluster was not updated yet after a failed switchover).
                    if (
                        not primary_endpoint
                        or primary_endpoint.split(":")[0] not in self._units_ips
                    ):
                        raise ValueError()
        except RetryError:
            return None
        else:
            return primary_endpoint

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """The leader removes the departing units from the list of cluster members."""
        # Don't handle this event in the same unit that is departing.
        if event.departing_unit == self.unit:
            # Set a flag to avoid deleting database users when this unit
            # is removed and receives relation broken events from related applications.
            self._peers.data[self.unit].update({"departing": "True"})
            return

        # Remove the departing member from the raft cluster.
        try:
            departing_member = event.departing_unit.name.replace("/", "-")
            member_ip = self._patroni.get_member_ip(departing_member)
            self._patroni.remove_raft_member(member_ip)
        except RemoveRaftMemberFailedError:
            event.defer()
            return

        # Allow leader to update the cluster members.
        if not self.unit.is_leader():
            return

        if "cluster_initialised" not in self._peers.data[self.app]:
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
            self._patroni.update_cluster_members()

            if self.primary_endpoint:
                self.postgresql_client_relation.update_endpoints()
            else:
                self.unit.status = BlockedStatus("no primary in the cluster")
                return

    def _on_pgdata_storage_detaching(self, _) -> None:
        # Change the primary if it's the unit that is being removed.
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
        except RetryError:
            # Ignore the event if the primary couldn't be retrieved.
            # If a switchover is needed, an automatic failover will be triggered
            # when the unit is removed.
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
            self.postgresql_client_relation.update_endpoints()
        else:
            self.unit.status = BlockedStatus("no primary in the cluster")

    def _on_peer_relation_changed(self, event: RelationChangedEvent):
        """Reconfigure cluster members when something changes."""
        # Prevents the cluster to be reconfigured before it's bootstrapped in the leader.
        if "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        # If the unit is the leader, it can reconfigure the cluster.
        if self.unit.is_leader():
            self._add_members(event)

        # Don't update this member before it's part of the members list.
        if self._unit_ip not in self.members_ips:
            return

        # Update the list of the cluster members in the replicas to make them know each other.
        try:
            # Update the members of the cluster in the Patroni configuration on this unit.
            self._patroni.update_cluster_members()
        except RetryError:
            self.unit.status = BlockedStatus("failed to update cluster members on member")
            return

        # Start can be called here multiple times as it's idempotent.
        # At this moment, it starts Patroni at the first time the data is received
        # in the relation.
        self._patroni.start_patroni()

        # Assert the member is up and running before marking the unit as active.
        if not self._patroni.member_started:
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        if self.primary_endpoint:
            self.postgresql_client_relation.update_endpoints()
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = BlockedStatus("no primary in the cluster")

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
                return

            logger.info("Reconfiguring cluster")
            self.unit.status = MaintenanceStatus("reconfiguring cluster")
            for member in self._hosts - self._patroni.cluster_members:
                logger.debug("Adding %s to cluster", member)
                self.add_cluster_member(member)
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
            self._patroni.update_cluster_members()
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
            self._storage_path,
            self._cluster_name,
            self._member_name,
            self.app.planned_units(),
            self._peer_members_ips,
            self._get_postgres_password(),
            self._replication_password,
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

    def _on_install(self, event) -> None:
        """Install prerequisites for the application."""
        self.unit.status = MaintenanceStatus("installing PostgreSQL")

        # Prevent the default cluster creation.
        self._inhibit_default_cluster_creation()

        # Install the PostgreSQL and Patroni requirements packages.
        try:
            self._install_apt_packages(event, ["postgresql", "python3-pip", "python3-psycopg2"])
        except (subprocess.CalledProcessError, apt.PackageNotFoundError):
            self.unit.status = BlockedStatus("failed to install apt packages")
            return

        try:
            resource_path = self.model.resources.fetch("patroni")
        except ModelError as e:
            logger.error(f"missing patroni resource {str(e)}")
            self.unit.status = BlockedStatus("Missing 'patroni' resource")
            return

        # Build Patroni package path with raft dependency and install it.
        try:
            patroni_package_path = f"{str(resource_path)}[raft]"
            self._install_pip_packages([patroni_package_path])
        except subprocess.SubprocessError:
            self.unit.status = BlockedStatus("failed to install Patroni python package")
            return

        self.unit.status = WaitingStatus("waiting to start PostgreSQL")

    def _inhibit_default_cluster_creation(self) -> None:
        """Stop the PostgreSQL packages from creating the default cluster."""
        os.makedirs(os.path.dirname(CREATE_CLUSTER_CONF_PATH), mode=0o755, exist_ok=True)
        with open(CREATE_CLUSTER_CONF_PATH, mode="w") as file:
            file.write("create_main_cluster = false\n")
            file.write(f"include '{self._storage_path}/conf.d/postgresql-operator.conf'")

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle the leader-elected event."""
        data = self._peers.data[self.app]
        # The leader sets the needed password on peer relation databag if they weren't set before.
        data.setdefault("postgres-password", new_password())
        data.setdefault("replication-password", new_password())

        # Update the list of the current PostgreSQL hosts when a new leader is elected.
        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._unit_ip not in self.members_ips:
            self._add_to_members_ips(self._unit_ip)

        # Remove departing units when the leader changes.
        for ip in self._get_ips_to_remove():
            self._remove_from_members_ips(ip)

        self._patroni.update_cluster_members()

        # Don't update connection endpoints in the first time this event run for
        # this application because there are no primary and replicas yet.
        if "cluster_initialised" not in self._peers.data[self.app]:
            return

        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        if self.primary_endpoint:
            self.postgresql_client_relation.update_endpoints()
        else:
            self.unit.status = BlockedStatus("no primary in the cluster")

    def _get_ips_to_remove(self) -> Set[str]:
        """List the IPs that were part of the cluster but departed."""
        old = self.members_ips
        current = self._units_ips
        return old - current

    def _on_start(self, event) -> None:
        """Handle the start event."""
        # Doesn't try to bootstrap the cluster if it's in a blocked state
        # caused, for example, because a failed installation of packages.
        if self._has_blocked_status:
            return

        postgres_password = self._get_postgres_password()
        replication_password = self._get_postgres_password()
        # If the leader was not elected (and the needed passwords were not generated yet),
        # the cluster cannot be bootstrapped yet.
        if not postgres_password or not replication_password:
            logger.info("leader not elected and/or passwords not yet generated")
            self.unit.status = WaitingStatus("awaiting passwords generation")
            event.defer()
            return

        if not self.unit.is_leader() and "cluster_initialised" not in self._peers.data[self.app]:
            self.unit.status = WaitingStatus("awaiting for cluster to start")
            event.defer()
            return

        # Only the leader can bootstrap the cluster.
        if not self.unit.is_leader():
            self._patroni.configure_patroni_on_unit()
            event.defer()
            return

        # Set some information needed by Patroni to bootstrap the cluster.
        if not self._patroni.bootstrap_cluster():
            self.unit.status = BlockedStatus("failed to start Patroni")
            return

        # Assert the member is up and running before marking it as initialised.
        if not self._patroni.member_started:
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        # Set the flag to enable the replicas to start the Patroni service.
        self._peers.data[self.app]["cluster_initialised"] = "True"
        self.unit.status = ActiveStatus()

    def _on_get_initial_password(self, event: ActionEvent) -> None:
        """Returns the password for the postgres user as an action response."""
        event.set_results({"postgres-password": self._get_postgres_password()})

    @property
    def _has_blocked_status(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    def _get_postgres_password(self) -> str:
        """Get postgres user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        data = self._peers.data[self.app]
        return data.get("postgres-password")

    @property
    def _replication_password(self) -> str:
        """Get replication user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        data = self._peers.data[self.app]
        return data.get("replication-password")

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

    def _install_pip_packages(self, packages: List[str]) -> None:
        """Simple wrapper around pip install.

        Raises:
            SubprocessError if the packages could not be installed.
        """
        try:
            command = [
                "pip3",
                "install",
                " ".join(packages),
            ]
            logger.debug(f"installing python packages: {', '.join(packages)}")
            subprocess.check_call(command)
        except subprocess.SubprocessError:
            logger.error("could not install pip packages")
            raise

    @property
    def _peers(self) -> Relation:
        """Fetch the peer relation.

        Returns:
             A:class:`ops.model.Relation` object representing
             the peer relation.
        """
        return self.model.get_relation(PEER)


if __name__ == "__main__":
    main(PostgresqlOperatorCharm)
