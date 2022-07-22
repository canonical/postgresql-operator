#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for the PostgreSQL database."""
import json
import logging
import secrets
import string
import subprocess
from typing import List, Set

from charms.operator_libs_linux.v0 import apt
from ops.charm import ActionEvent, CharmBase
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

from cluster import Patroni, SwitchoverFailedError

logger = logging.getLogger(__name__)

PEER = "postgresql-replicas"


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.get_initial_password_action, self._on_get_initial_password)
        self._cluster = Patroni(self._unit_ip)

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

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

    def _get_peer_unit_ip(self, unit: Unit) -> str:
        """Get the IP address of a specific peer unit."""
        return self._peers.data[unit].get("private-address")

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
        addresses = {self._get_peer_unit_ip(unit) for unit in self._peers.units}
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

    @property
    def _unit_ip(self) -> str:
        """Current unit ip."""
        return self.model.get_binding(PEER).network.bind_address

    def _on_install(self, event) -> None:
        """Install prerequisites for the application."""
        self.unit.status = MaintenanceStatus("installing PostgreSQL")

        # Prevent the default cluster creation.
        self._cluster.inhibit_default_cluster_creation()

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

    def _on_leader_elected(self, _) -> None:
        """Handle the leader-elected event."""
        data = self._peers.data[self.app]
        # The leader sets the needed password on peer relation databag if they weren't set before.
        data.setdefault("postgres-password", self._new_password())
        data.setdefault("replication-password", self._new_password())

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

        # Set some information needed by Patroni to bootstrap the cluster.
        cluster_name = self.app.name
        member_name = self.unit.name.replace("/", "-")
        success = self._cluster.bootstrap_cluster(
            cluster_name, member_name, postgres_password, replication_password
        )
        if success:
            # The cluster is up and running.
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = BlockedStatus("failed to start Patroni")

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
