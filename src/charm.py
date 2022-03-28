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

from cluster import (
    ClusterAlreadyRunningError,
    ClusterCreateError,
    ClusterNotRunningError,
    ClusterStartError,
    PostgresqlCluster,
)

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.get_initial_password_action, self._on_get_initial_password)
        self._cluster = PostgresqlCluster()

    def _on_install(self, event) -> None:
        """Install prerequisites for the application."""
        self.unit.status = MaintenanceStatus("installing PostgreSQL")

        # Prevent the default cluster creation.
        self._cluster.inhibit_default_cluster_creation()

        # Install the PostgreSQL and Patroni requirements packages.
        self._install_apt_packages(event, ["postgresql", "python3-pip", "python3-psycopg2"])

        try:
            resource_path = self.model.resources.fetch("patroni")
        except ModelError as e:
            logger.error(f"missing patroni resource {str(e)}")
            self.unit.status = BlockedStatus("Missing 'patroni' resource")
            return

        # Build Patroni package path with raft dependency and install it.
        patroni_package_path = f"{str(resource_path)}[raft]"
        self._install_pip_packages([patroni_package_path])

        self.unit.status = WaitingStatus("waiting to start PostgreSQL")

    def _on_leader_elected(self, _) -> None:
        """Handle the leader-elected event."""
        data = self._peers.data[self.app]
        postgres_password = data.get("postgres-password", None)

        if postgres_password is None:
            self._peers.data[self.app]["postgres-password"] = self._new_password()

    def _on_start(self, event) -> None:
        password = self._get_postgres_password()
        # If the leader was elected and it generated a superuser password for the all the units,
        # the cluster can be bootstrapped in each unit.
        if password is not None:
            try:
                self._cluster.bootstrap_cluster(password)
            except ClusterAlreadyRunningError:
                logging.error("there is already a running cluster")
                self.unit.status = BlockedStatus("there is already a running cluster")
            except ClusterCreateError as e:
                logging.error("failed to create cluster")
                self.unit.status = BlockedStatus(f"failed to create cluster with error {e}")
            except (ClusterNotRunningError, ClusterStartError) as e:
                logging.error("failed to start cluster")
                self.unit.status = BlockedStatus(f"failed to start cluster with error {e}")
            except subprocess.CalledProcessError as e:
                logging.error("failed to bootstrap cluster")
                self.unit.status = BlockedStatus(f"failed to bootstrap cluster with error {e}")
            else:
                # The cluster is up and running.
                self.unit.status = ActiveStatus()
        else:
            logger.info("leader not elected and/or superuser password not yet generated")
            self.unit.status = WaitingStatus("waiting superuser password generation")
            event.defer()

    def _on_get_initial_password(self, event: ActionEvent) -> None:
        """Returns the password for the postgres user as an action response."""
        event.set_results({"postgres-password": self._get_postgres_password()})

    def _get_postgres_password(self) -> str:
        """Get postgres user password.

        Returns:
            The password from the peer relation or None if the
            password has not yet been set by the leader.
        """
        data = self._peers.data[self.app]
        return data.get("postgres-password", None)

    def _install_apt_packages(self, _, packages: List[str]) -> None:
        """Simple wrapper around 'apt-get install -y."""
        try:
            logger.debug("updating apt cache")
            apt.update()
        except subprocess.CalledProcessError as e:
            logger.exception("failed to update apt cache, CalledProcessError", exc_info=e)
            self.unit.status = BlockedStatus("failed to update apt cache")
            return

        try:
            logger.debug(f"installing apt packages: {', '.join(packages)}")
            apt.add_package(packages)
        except apt.PackageNotFoundError:
            logger.error("a specified package not found in package cache or on system")
            self.unit.status = BlockedStatus("failed to install packages")

    def _install_pip_packages(self, packages: List[str]) -> None:
        """Simple wrapper around pip install."""
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
            self.unit.status = BlockedStatus("failed to install pip packages")

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
        return self.model.get_relation("postgresql-replicas")


if __name__ == "__main__":
    main(PostgresqlOperatorCharm)
