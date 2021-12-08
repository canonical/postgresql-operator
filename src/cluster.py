#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage cluster lifecycle."""

import logging
import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path

from charms.operator_libs_linux.v0.apt import DebianPackage
from jinja2 import Template

logger = logging.getLogger(__name__)

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"


class ClusterAlreadyRunningError(Exception):
    """Exception raised when there is already a running cluster."""

    pass


class ClusterCreateError(Exception):
    """Exception raised by an error on cluster creation."""

    pass


class ClusterNotRunningError(Exception):
    """Exception raised when cluster is not running after start call."""

    pass


class ClusterStartError(Exception):
    """Exception raised by an error on cluster start."""

    pass


class PostgresqlCluster:
    """This class handles the creation, start and listing of PostgreSQL clusters.

    A PostgreSQL cluster is a collection of databases that is managed by a single instance of a
    running database server.
    """

    def __init__(self):
        self.version = self._get_postgresql_version()
        self.conf_path = Path(f"/etc/postgresql/{self.version}/main")

    def bootstrap_cluster(self, password: str) -> None:
        """Bootstrap a PostgreSQL cluster with the given superuser password."""
        # Check for no running clusters (like the default cluster created on postgres install).
        if self._is_cluster_running():
            raise ClusterAlreadyRunningError()
        else:
            # Create a new cluster.
            self._create_cluster(password)
            # Render the configuration files and start the cluster.
            self._copy_pg_hba_conf_file()
            self._render_postgresql_conf_file()
            self._start_cluster()
            # Check that the cluster is up and running.
            if not self._is_cluster_running():
                raise ClusterNotRunningError()

    def inhibit_default_cluster_creation(self) -> None:
        """Stop the PostgreSQL packages from creating the default cluster."""
        os.makedirs(os.path.dirname(CREATE_CLUSTER_CONF_PATH), mode=0o755, exist_ok=True)
        with open(CREATE_CLUSTER_CONF_PATH, mode="w") as file:
            file.write("create_main_cluster = false\n")
            file.write(f"include '{self.conf_path}/conf.d/postgresql-operator.conf'")

    def _copy_pg_hba_conf_file(self) -> None:
        """Copy the application's auth configuration file to the right directory."""
        shutil.copyfile("src/pg_hba.conf", f"{self.conf_path}/pg_hba.conf")

    def _create_cluster(self, password: str) -> None:
        """Create a new PostgreSQL cluster."""
        # Write the password in a temporary file for pg_createcluster.
        temp = tempfile.NamedTemporaryFile(delete=False)
        with open(temp.name, mode="w") as file:
            file.write(password)
        # Change the owner of the file in order to it be read by initdb command.
        u = pwd.getpwnam("postgres")
        os.chown(temp.name, uid=u.pw_uid, gid=u.pw_gid)

        # Run the create cluster command.
        try:
            command = [
                "pg_createcluster",
                self.version,
                "main",
                "--datadir=/var/lib/postgresql/data/pgdata",
                "--",
                f"--pwfile={temp.name}",
            ]
            logger.debug(f"pg_createcluster call: {' '.join(command)}")
            subprocess.check_call(command)
        except subprocess.CalledProcessError as e:
            raise ClusterCreateError(e.stdout)
        finally:
            # Remove the temporary file.
            os.remove(temp.name)

    def _get_postgresql_version(self) -> str:
        """Return the PostgreSQL version from the system."""
        package = DebianPackage.from_system("postgresql")
        # Remove the Ubuntu revision from the version.
        return str(package.version).split("+")[0]

    def _is_cluster_running(self) -> bool:
        """Check whether the cluster is running or not."""
        try:
            clusters = (
                subprocess.check_output(["pg_lsclusters", "--no-header"]).decode().splitlines()
            )
            online_clusters = list(filter(lambda x: x.split()[3] == "online", clusters))
            return len(online_clusters) == 1
        except subprocess.CalledProcessError:
            raise

    def _render_file(self, path: str, content: str, mode: int) -> None:
        """Write a content rendered from a template to a file.

        Args:
            path: the path to the file.
            content: the data to be written to the file.
            mode: access permission mask applied to the
              file using chmod (e.g. 0o640).
        """
        # TODO: keep this method to use it also for generating replication configuration files and
        # move it to an utils / helpers file.
        # Write the content to the file.
        with open(path, "w+") as file:
            file.write(content)
        # Ensure correct permissions are set on the file.
        os.chmod(path, mode)
        # Get the uid/gid for the postgres user.
        u = pwd.getpwnam("postgres")
        # Set the correct ownership for the file.
        os.chown(path, uid=u.pw_uid, gid=u.pw_gid)

    def _render_postgresql_conf_file(self) -> None:
        """Render the PostgreSQL configuration file."""
        # Open the template postgresql.conf file.
        with open("templates/postgresql.conf.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        # TODO: add extra configurations here later.
        rendered = template.render(listen_addresses="*")
        self._render_file(f"{self.conf_path}/conf.d/postgresql-operator.conf", rendered, 0o644)

    def _start_cluster(self) -> None:
        """Start a PostgreSQL cluster."""
        try:
            command = [
                "pg_ctlcluster",
                self.version,
                "main",
                "start",
            ]
            logger.debug(f"pg_ctlcluster call: {' '.join(command)}")
            subprocess.check_call(command)
        except subprocess.CalledProcessError as e:
            raise ClusterStartError(e.stdout)
