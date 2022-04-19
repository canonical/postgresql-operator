#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage cluster lifecycle."""

import logging
import os
import pwd
from pathlib import Path

import yaml
from charms.operator_libs_linux.v0.apt import DebianPackage
from charms.operator_libs_linux.v1.systemd import (
    daemon_reload,
    service_running,
    service_start,
)
from jinja2 import Template

logger = logging.getLogger(__name__)

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
STORAGE_PATH = METADATA["storage"]["pgdata"]["location"]
PATRONI_SERVICE = "patroni"


class Patroni:
    """This class handles the bootstrap of a PostgreSQL database through Patroni."""

    def __init__(self, unit_ip: str):
        self.unit_ip = unit_ip

    def bootstrap_cluster(
        self,
        cluster_name: str,
        member_name: str,
        superuser_password: str,
        replication_password: str,
    ) -> bool:
        """Bootstrap a PostgreSQL cluster using Patroni.

        Args:
            cluster_name: name of the cluster
            member_name: name of the member inside the cluster
            superuser_password: password for the postgres user
            replication_password: password for the user used in the replication
        """
        # Render the configuration files and start the cluster.
        self._change_owner(STORAGE_PATH)
        self._render_patroni_yml_file(
            cluster_name, member_name, superuser_password, replication_password
        )
        self._render_patroni_service_file()
        # Reload systemd services before trying to start Patroni.
        daemon_reload()
        self._render_postgresql_conf_file()
        return self._start_patroni()

    def _change_owner(self, path: str) -> None:
        """Change the ownership of a file or a directory to the postgres user.

        Args:
            path: path to a file or directory.
        """
        # Get the uid/gid for the postgres user.
        user_database = pwd.getpwnam("postgres")
        # Set the correct ownership for the file or directory.
        os.chown(path, uid=user_database.pw_uid, gid=user_database.pw_gid)

    def _create_directory(self, path: str, mode: int) -> None:
        """Creates a directory.

        Args:
            path: the path of the directory that should be created.
            mode: access permission mask applied to the
              directory using chmod (e.g. 0o640).
        """
        os.makedirs(path, mode=mode, exist_ok=True)
        # Ensure correct permissions are set on the directory.
        os.chmod(path, mode)
        self._change_owner(path)

    def inhibit_default_cluster_creation(self) -> None:
        """Stop the PostgreSQL packages from creating the default cluster."""
        os.makedirs(os.path.dirname(CREATE_CLUSTER_CONF_PATH), mode=0o755, exist_ok=True)
        with open(CREATE_CLUSTER_CONF_PATH, mode="w") as file:
            file.write("create_main_cluster = false\n")
            file.write(f"include '{STORAGE_PATH}/conf.d/postgresql-operator.conf'")

    def _get_postgresql_version(self) -> str:
        """Return the PostgreSQL version from the system."""
        package = DebianPackage.from_system("postgresql")
        # Remove the Ubuntu revision from the version.
        return str(package.version).split("+")[0]

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
        self._change_owner(path)

    def _render_patroni_service_file(self) -> None:
        """Render the Patroni configuration file."""
        # Open the template patroni systemd unit file.
        with open("templates/patroni.service.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(conf_path=STORAGE_PATH)
        self._render_file("/etc/systemd/system/patroni.service", rendered, 0o644)

    def _render_patroni_yml_file(
        self,
        cluster_name: str,
        member_name: str,
        superuser_password: str,
        replication_password: str,
    ) -> None:
        """Render the Patroni configuration file.

        Args:
            cluster_name: name of the cluster
            member_name: name of the member inside the cluster
            superuser_password: password for the postgres user
            replication_password: password for the user used in the replication
        """
        # Open the template patroni.yml file.
        with open("templates/patroni.yml.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            conf_path=STORAGE_PATH,
            member_name=member_name,
            scope=cluster_name,
            self_ip=self.unit_ip,
            superuser_password=superuser_password,
            replication_password=replication_password,
            version=self._get_postgresql_version(),
        )
        self._render_file(f"{STORAGE_PATH}/patroni.yml", rendered, 0o644)

    def _render_postgresql_conf_file(self) -> None:
        """Render the PostgreSQL configuration file."""
        # Open the template postgresql.conf file.
        with open("templates/postgresql.conf.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        # TODO: add extra configurations here later.
        rendered = template.render(listen_addresses="*")
        self._create_directory(f"{STORAGE_PATH}/conf.d", mode=0o644)
        self._render_file(f"{STORAGE_PATH}/conf.d/postgresql-operator.conf", rendered, 0o644)

    def _start_patroni(self) -> bool:
        """Start Patroni service using systemd.

        Returns:
            Whether the service started successfully.
        """
        service_start(PATRONI_SERVICE)
        return service_running(PATRONI_SERVICE)
