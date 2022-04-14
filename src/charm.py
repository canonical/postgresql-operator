#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for the PostgreSQL database."""

import logging
import secrets
import string
import subprocess
from typing import List

from charms.operator_libs_linux.v0 import apt
from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    Relation,
    WaitingStatus,
)

from cluster import Patroni

logger = logging.getLogger(__name__)

PEER = "postgresql-replicas"


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.get_initial_password_action, self._on_get_initial_password)
        self._cluster = Patroni(self._unit_ip)

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
        if postgres_password is not None and replication_password is not None:
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
        else:
            logger.info("leader not elected and/or superuser password not yet generated")
            self.unit.status = WaitingStatus("waiting passwords generation")
            event.defer()

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
