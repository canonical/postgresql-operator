#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage cluster lifecycle."""
import logging
import os
import pwd
import subprocess
from typing import Set

import requests
from charms.operator_libs_linux.v0.apt import DebianPackage
from charms.operator_libs_linux.v1.systemd import (
    daemon_reload,
    service_restart,
    service_resume,
    service_running,
    service_start,
)
from jinja2 import Template
from tenacity import (
    AttemptManager,
    RetryError,
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

from constants import (
    API_REQUEST_TIMEOUT,
    PATRONI_CLUSTER_STATUS_ENDPOINT,
    REWIND_USER,
    TLS_CA_FILE,
    USER,
)

logger = logging.getLogger(__name__)

PATRONI_SERVICE = "patroni"


class NotReadyError(Exception):
    """Raised when not all cluster members healthy or finished initial sync."""


class RemoveRaftMemberFailedError(Exception):
    """Raised when a remove raft member failed for some reason."""


class SwitchoverFailedError(Exception):
    """Raised when a switchover failed for some reason."""


class Patroni:
    """This class handles the bootstrap of a PostgreSQL database through Patroni."""

    pass

    def __init__(
        self,
        unit_ip: str,
        storage_path: str,
        cluster_name: str,
        member_name: str,
        peers_ips: Set[str],
        superuser_password: str,
        replication_password: str,
        rewind_password: str,
        tls_enabled: bool,
    ):
        """Initialize the Patroni class.

        Args:
            unit_ip: IP address of the current unit
            storage_path: path to the storage mounted on this unit
            cluster_name: name of the cluster
            member_name: name of the member inside the cluster
            peers_ips: IP addresses of the peer units
            superuser_password: password for the operator user
            replication_password: password for the user used in the replication
            rewind_password: password for the user used on rewinds
            tls_enabled: whether TLS is enabled
        """
        self.unit_ip = unit_ip
        self.storage_path = storage_path
        self.cluster_name = cluster_name
        self.member_name = member_name
        self.peers_ips = peers_ips
        self.superuser_password = superuser_password
        self.replication_password = replication_password
        self.rewind_password = rewind_password
        self.tls_enabled = tls_enabled
        # Variable mapping to requests library verify parameter.
        # The CA bundle file is used to validate the server certificate when
        # TLS is enabled, otherwise True is set because it's the default value.
        self.verify = f"{self.storage_path}/{TLS_CA_FILE}" if tls_enabled else True

    @property
    def _patroni_url(self) -> str:
        """Patroni REST API URL."""
        return f"{'https' if self.tls_enabled else 'http'}://{self.unit_ip}:8008"

    def bootstrap_cluster(self) -> bool:
        """Bootstrap a PostgreSQL cluster using Patroni."""
        # Render the configuration files and start the cluster.
        self.configure_patroni_on_unit()
        return self.start_patroni()

    def configure_patroni_on_unit(self):
        """Configure Patroni (configuration files and service) on the unit."""
        self._change_owner(self.storage_path)
        # Avoid rendering the Patroni config file if it was already rendered.
        if not os.path.exists(f"{self.storage_path}/patroni.yml"):
            self.render_patroni_yml_file()
        self._render_patroni_service_file()
        # Reload systemd services before trying to start Patroni.
        daemon_reload()

    def _change_owner(self, path: str) -> None:
        """Change the ownership of a file or a directory to the postgres user.

        Args:
            path: path to a file or directory.
        """
        # Get the uid/gid for the postgres user.
        user_database = pwd.getpwnam("postgres")
        # Set the correct ownership for the file or directory.
        os.chown(path, uid=user_database.pw_uid, gid=user_database.pw_gid)

    @property
    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=10))
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        cluster_status = requests.get(
            f"{self._patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
            verify=self.verify,
            timeout=API_REQUEST_TIMEOUT,
        )
        return set([member["name"] for member in cluster_status.json()["members"]])

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

    def _get_postgresql_version(self) -> str:
        """Return the PostgreSQL version from the system."""
        package = DebianPackage.from_system("postgresql")
        # Remove the Ubuntu revision from the version.
        return str(package.version).split("+")[0]

    def get_member_ip(self, member_name: str) -> str:
        """Get cluster member IP address.

        Args:
            member_name: cluster member name.

        Returns:
            IP address of the cluster member.
        """
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(2 * len(self.peers_ips) + 1)):
            with attempt:
                url = self._get_alternative_patroni_url(attempt)
                cluster_status = requests.get(
                    f"{url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                    verify=self.verify,
                    timeout=API_REQUEST_TIMEOUT,
                )
                for member in cluster_status.json()["members"]:
                    if member["name"] == member_name:
                        return member["host"]

    def get_primary(self, unit_name_pattern=False) -> str:
        """Get primary instance.

        Args:
            unit_name_pattern: whether to convert pod name to unit name

        Returns:
            primary pod or unit name.
        """
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(2 * len(self.peers_ips) + 1)):
            with attempt:
                url = self._get_alternative_patroni_url(attempt)
                cluster_status = requests.get(
                    f"{url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                    verify=self.verify,
                    timeout=API_REQUEST_TIMEOUT,
                )
                for member in cluster_status.json()["members"]:
                    if member["role"] == "leader":
                        primary = member["name"]
                        if unit_name_pattern:
                            # Change the last dash to / in order to match unit name pattern.
                            primary = "/".join(primary.rsplit("-", 1))
                        return primary

    def _get_alternative_patroni_url(self, attempt: AttemptManager) -> str:
        """Get an alternative REST API URL from another member each time.

        When the Patroni process is not running in the current unit it's needed
        to use a URL from another cluster member REST API to do some operations.
        """
        attempt_number = attempt.retry_state.attempt_number
        if attempt_number > 1:
            url = self._patroni_url
            # Build the URL using http and later using https for each peer.
            if (attempt_number - 1) <= len(self.peers_ips):
                url = url.replace("https://", "http://")
                unit_number = attempt_number - 2
            else:
                url = url.replace("http://", "https://")
                unit_number = attempt_number - 2 - len(self.peers_ips)
            other_unit_ip = list(self.peers_ips)[unit_number]
            url = url.replace(self.unit_ip, other_unit_ip)
        else:
            url = self._patroni_url
        return url

    def are_all_members_ready(self) -> bool:
        """Check if all members are correctly running Patroni and PostgreSQL.

        Returns:
            True if all members are ready False otherwise. Retries over a period of 10 seconds
            3 times to allow server time to start up.
        """
        # Request info from cluster endpoint
        # (which returns all members of the cluster and their states).
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    cluster_status = requests.get(
                        f"{self._patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                        verify=self.verify,
                        timeout=API_REQUEST_TIMEOUT,
                    )
        except RetryError:
            return False

        # Check if all members are running and one of them is a leader (primary),
        # because sometimes there may exist (for some period of time) only
        # replicas after a failed switchover.
        return all(
            member["state"] == "running" for member in cluster_status.json()["members"]
        ) and any(member["role"] == "leader" for member in cluster_status.json()["members"])

    @property
    def member_started(self) -> bool:
        """Has the member started Patroni and PostgreSQL.

        Returns:
            True if services is ready False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(
                        f"{self._patroni_url}/health",
                        verify=self.verify,
                        timeout=API_REQUEST_TIMEOUT,
                    )
        except RetryError:
            return False

        return r.json()["state"] == "running"

    @property
    def member_replication_lag(self) -> str:
        """Member replication lag."""
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    cluster_status = requests.get(
                        f"{self._patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                        verify=self.verify,
                        timeout=API_REQUEST_TIMEOUT,
                    )
        except RetryError:
            return "unknown"

        for member in cluster_status.json()["members"]:
            if member["name"] == self.member_name:
                return member["lag"]

        return "unknown"

    def render_file(self, path: str, content: str, mode: int) -> None:
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
        rendered = template.render(conf_path=self.storage_path)
        self.render_file("/etc/systemd/system/patroni.service", rendered, 0o644)

    def render_patroni_yml_file(self, enable_tls: bool = False) -> None:
        """Render the Patroni configuration file.

        Args:
            enable_tls: whether to enable TLS.
        """
        # Open the template patroni.yml file.
        with open("templates/patroni.yml.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            conf_path=self.storage_path,
            enable_tls=enable_tls,
            member_name=self.member_name,
            peers_ips=self.peers_ips,
            scope=self.cluster_name,
            self_ip=self.unit_ip,
            superuser=USER,
            superuser_password=self.superuser_password,
            replication_password=self.replication_password,
            rewind_user=REWIND_USER,
            rewind_password=self.rewind_password,
            version=self._get_postgresql_version(),
        )
        self.render_file(f"{self.storage_path}/patroni.yml", rendered, 0o644)

    def start_patroni(self) -> bool:
        """Start Patroni service using systemd.

        Returns:
            Whether the service started successfully.
        """
        # Start the service and enable it to start at boot.
        service_start(PATRONI_SERVICE)
        service_resume(PATRONI_SERVICE)
        return service_running(PATRONI_SERVICE)

    def switchover(self) -> None:
        """Trigger a switchover."""
        # Try to trigger the switchover.
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                current_primary = self.get_primary()
                r = requests.post(
                    f"{self._patroni_url}/switchover",
                    json={"leader": current_primary},
                    verify=self.verify,
                )

        # Check whether the switchover was unsuccessful.
        if r.status_code != 200:
            raise SwitchoverFailedError(f"received {r.status_code}")

    def failover(self) -> None:
        """Trigger a switchover."""
        # Try to trigger the switchover.
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                r = requests.post(
                    f"{self._patroni_url}/failover",
                    json={"candidate": self.member_name},
                    verify=self.verify,
                )

        # Check whether the switchover was unsuccessful.
        if r.status_code != 200:
            raise SwitchoverFailedError(f"received {r.status_code}")

    @retry(
        retry=retry_if_result(lambda x: not x),
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def primary_changed(self, old_primary: str) -> bool:
        """Checks whether the primary unit has changed."""
        primary = self.get_primary()
        return primary != old_primary

    def remove_raft_member(self, member_ip: str) -> None:
        """Remove a member from the raft cluster.

        The raft cluster is a different cluster from the Patroni cluster.
        It is responsible for defining which Patroni member can update
        the primary member in the DCS.

        Raises:
            RaftMemberNotFoundError: if the member to be removed
                is not part of the raft cluster.
        """
        # Get the status of the raft cluster.
        raft_status = subprocess.check_output(
            ["syncobj_admin", "-conn", "127.0.0.1:2222", "-status"]
        ).decode("UTF-8")

        # Check whether the member is still part of the raft cluster.
        if not member_ip or member_ip not in raft_status:
            return

        # Remove the member from the raft cluster.
        result = subprocess.check_output(
            ["syncobj_admin", "-conn", "127.0.0.1:2222", "-remove", f"{member_ip}:2222"]
        ).decode("UTF-8")
        if "SUCCESS" not in result:
            raise RemoveRaftMemberFailedError()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reload_patroni_configuration(self):
        """Reload Patroni configuration after it was changed."""
        requests.post(f"{self._patroni_url}/reload", verify=self.verify)

    def restart_patroni(self) -> bool:
        """Restart Patroni.

        Returns:
            Whether the service restarted successfully.
        """
        service_restart(PATRONI_SERVICE)
        return service_running(PATRONI_SERVICE)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def restart_postgresql(self) -> None:
        """Restart PostgreSQL."""
        requests.post(f"{self._patroni_url}/restart", verify=self.verify)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reinitialize_postgresql(self) -> None:
        """Reinitialize PostgreSQL."""
        requests.post(f"{self._patroni_url}/reinitialize", verify=self.verify)
