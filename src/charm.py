#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for the PostgreSQL database."""
import json
import logging
import os
import secrets
import string
import subprocess
from typing import List

from charms.operator_libs_linux.v0 import apt
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
from tenacity import RetryError, retry, stop_after_delay, wait_fixed

from cluster import NotReadyError, Patroni, SwitchoverFailedError

# from requests import HTTPError


logger = logging.getLogger(__name__)

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"
PEER = "postgresql-replicas"


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

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        # Allow leader to update hosts if it isn't leaving.
        if not self.unit.is_leader() or event.departing_unit == self.unit:
            return

        # Remove departing members.
        self._remove_members(event)

    def _remove_members(self, event):
        """Remove cluster members one at a time."""
        try:
            logger.error(self.members_ips)
            logger.error(self._units_ips)
            members_ips = set(json.loads(self.members_ips))
            for member_ip in members_ips - set(self._units_ips):
                # Check that all members are ready before removing unit from the cluster.
                if not self._patroni.are_all_members_ready():
                    raise NotReadyError("not all members are ready")

                # Update the list of the current members.
                self._update_members_ips(ip_to_remove=member_ip)
                self._patroni.update_cluster_members()
        except NotReadyError:
            logger.info("Deferring reconfigure: another member doing sync right now")
            event.defer()

    def _on_pgdata_storage_detaching(self, _) -> None:
        # Change the primary if it's the unit that is being removed.
        if self.unit.name == self._patroni.get_primary(unit_name_pattern=True):
            self._change_primary()

    @retry(
        stop=stop_after_delay(60),
        wait=wait_fixed(5),
        reraise=True,
    )
    def _change_primary(self) -> None:
        """Change the primary member of the cluster."""
        # Inform the first of the remaining available members to not incur the risk
        # of triggering a switchover to a member that is also being removed.
        if not self._patroni.are_all_members_ready():
            raise NotReadyError("not all members are ready")

        # Try switchover and raise and exception if it doesn't succeed.
        # If it doesn't happen on time, Patroni will automatically run a fail-over.
        try:
            self._patroni.switchover()
            logger.info("successful switchover")
        except SwitchoverFailedError as e:
            logger.error(f"switchover failed with reason: {e}")

    def _on_peer_relation_changed(self, event: RelationChangedEvent):
        """Reconfigure cluster members when something changes."""
        # Prevents the cluster to be reconfigured before it's bootstrapped in the leader.
        if "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        # If the unit is the leader, it can reconfigure the cluster.
        if self.unit.is_leader():
            self._reconfigure_cluster(event)

        if self._unit_ip in json.loads(self.members_ips):
            # Update the list of the cluster members in the replicas to make them know each other.
            try:
                # Update the members of the cluster in the Patroni configuration on this unit.
                self._patroni.update_cluster_members()
                # Start can be called here multiple times as it's idempotent.
                # At this moment, it starts Patroni at the first time the data is received
                # in the relation.
                self._patroni.start_patroni()
                # Assert the cluster is up and running before marking the unit as active.
                try:
                    if not self._patroni.cluster_started():
                        raise NotReadyError
                except (NotReadyError, RetryError):
                    self.unit.status = WaitingStatus("awaiting for cluster to start")
                    event.defer()
                    return
                self.unit.status = ActiveStatus()
            except RetryError:
                self.unit.status = BlockedStatus("failed to update cluster members on member")

    def _reconfigure_cluster(self, event):
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

    def add_cluster_member(self, member: str) -> None:
        """Add a new member to the cluster at a time."""
        unit = self.model.get_unit("/".join(member.rsplit("-", 1)))
        member_ip = self._get_ip_by_unit(unit)

        if not self._patroni.are_all_members_ready():
            raise NotReadyError("not all members are ready")

        # Update the current list of members of the cluster.
        self._update_members_ips(ip_to_add=member_ip)

        # Update Patroni configuration file.
        try:
            logger.error(f"members: {self.members_ips}")
            self._patroni.update_cluster_members()
        except RetryError:
            self.unit.status = BlockedStatus("failed to update cluster members on member")

    def _get_ip_by_unit(self, unit: Unit) -> str:
        """Get the IP address of a specific unit."""
        return self._peers.data[unit].get("private-address")

    @property
    def _hosts(self) -> set:
        """Get the list of units that are currently deployed."""
        peers = self.model.get_relation(PEER)
        hosts = [self.unit.name.replace("/", "-")] + [
            unit.name.replace("/", "-") for unit in peers.units
        ]
        return set(hosts)

    @property
    def _patroni(self):
        """Returns an instance of the Patroni object."""
        return Patroni(
            self._unit_ip,
            self._storage_path,
            self._cluster_name,
            self._member_name,
            self._peers_ips,
            self._get_postgres_password(),
            self._replication_password,
        )

    @property
    def _peers_ips(self) -> List[str]:
        """Fetch current list of peers IPs.

        Returns:
            A list of peers addresses (strings).
        """
        # Get all members IPs and remove the current unit IP from the list.
        addresses = json.loads(self.members_ips)
        current_unit_ip = self._unit_ip
        if current_unit_ip in addresses:
            addresses.remove(current_unit_ip)
        return addresses

    @property
    def _units_ips(self) -> List[str]:
        """Fetch current list of peers IPs.

        Returns:
            A list of peers addresses (strings).
        """
        # Get all members IPs and remove the current unit IP from the list.
        addresses = [self._get_ip_by_unit(unit) for unit in self._peers.units]
        addresses.append(self._unit_ip)
        return addresses

    @property
    def members_ips(self):
        """Returns the list of IPs addresses of the current members of the cluster."""
        return self._peers.data[self.app].get("members_ips", "[]")

    def _update_members_ips(self, ip_to_add: str = None, ip_to_remove: str = None) -> None:
        """Update cluster members IPs."""
        # Allow leader to reset which members are part of the cluster.
        if not self.unit.is_leader():
            return

        ips = json.loads(self._peers.data[self.app].get("members_ips", "[]"))
        if ip_to_add and ip_to_add not in ips:
            ips.append(ip_to_add)
        elif ip_to_remove:
            logger.error(ips)
            logger.error(type(ips))
            logger.error(ip_to_remove)
            logger.error(type(ip_to_remove))
            ips.remove(ip_to_remove)
        self._peers.data[self.app]["members_ips"] = json.dumps(ips)

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
        data.setdefault("postgres-password", self._new_password())
        data.setdefault("replication-password", self._new_password())

        # # Update the list of the current PostgreSQL hosts when a new leader is elected.
        # self._update_members_ips(ip_to_add=self._unit_ip)
        #
        # # Remove departing members.
        # if self._unit_ip not in json.loads(self.members_ips):
        #     try:
        #         self._remove_members(event)
        #     except RetryError:
        #         # Ignore RetryError on first leader election.
        #         pass
        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        restart = True
        if self._unit_ip not in json.loads(self.members_ips):
            self._update_members_ips(ip_to_add=self._unit_ip)
            restart = False

        # Remove departing units when the leader changes.
        for ip in self._get_endpoints_to_remove():
            self._update_members_ips(ip_to_remove=ip)

        self._patroni.update_cluster_members(restart)

    def _get_endpoints_to_remove(self) -> List[str]:
        """List the endpoints that were part of the cluster but departed."""
        old = json.loads(self.members_ips)
        current = self._units_ips
        endpoints_to_remove = list(set(old) - set(current))
        return endpoints_to_remove

    def _on_start(self, event) -> None:
        """Handle the start event."""
        # Doesn't try to bootstrap the cluster if it's in a blocked state
        # caused, for example, because a failed installation of packages.
        if self._has_blocked_status:
            return

        postgres_password = self._get_postgres_password()
        replication_password = self._get_postgres_password()
        # If the leader was elected and it generated the needed passwords,
        # the cluster can be bootstrapped.
        if not postgres_password or not replication_password:
            logger.info("leader not elected and/or superuser password not yet generated")
            self.unit.status = WaitingStatus("waiting passwords generation")
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

        # Assert the cluster is up and running before marking it as initialised.
        try:
            if not self._patroni.cluster_started():
                self.unit.status = WaitingStatus("awaiting for cluster to start")
                event.defer()
                return
        except RetryError:
            self.unit.status = WaitingStatus("awaiting for cluster to start")
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

    def _new_password(self) -> str:
        """Generate a random password string.

        Returns:
           A random password string.
        """
        choices = string.ascii_letters + string.digits
        password = "".join([secrets.choice(choices) for i in range(16)])
        return password

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
