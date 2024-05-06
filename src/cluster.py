#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage cluster lifecycle."""

import logging
import os
import pwd
import subprocess
from typing import Any, Dict, List, Optional, Set

import requests
from charms.operator_libs_linux.v2 import snap
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
    PATRONI_CONF_PATH,
    PATRONI_LOGS_PATH,
    PGBACKREST_CONFIGURATION_FILE,
    POSTGRESQL_CONF_PATH,
    POSTGRESQL_DATA_PATH,
    POSTGRESQL_LOGS_PATH,
    POSTGRESQL_SNAP_NAME,
    REWIND_USER,
    TLS_CA_FILE,
    USER,
)

logger = logging.getLogger(__name__)

PG_BASE_CONF_PATH = f"{POSTGRESQL_CONF_PATH}/postgresql.conf"

RUNNING_STATES = ["running", "streaming"]


class ClusterNotPromotedError(Exception):
    """Raised when a cluster is not promoted."""


class NotReadyError(Exception):
    """Raised when not all cluster members healthy or finished initial sync."""


class EndpointNotReadyError(Exception):
    """Raised when an endpoint is not ready."""


class StandbyClusterAlreadyPromotedError(Exception):
    """Raised when a standby cluster is already promoted."""


class RemoveRaftMemberFailedError(Exception):
    """Raised when a remove raft member failed for some reason."""


class SwitchoverFailedError(Exception):
    """Raised when a switchover failed for some reason."""


class UpdateSyncNodeCountError(Exception):
    """Raised when updating synchronous_node_count failed for some reason."""


class Patroni:
    """This class handles the bootstrap of a PostgreSQL database through Patroni."""

    def __init__(
        self,
        charm,
        unit_ip: str,
        cluster_name: str,
        member_name: str,
        planned_units: int,
        peers_ips: Set[str],
        superuser_password: str,
        replication_password: str,
        rewind_password: str,
        tls_enabled: bool,
    ):
        """Initialize the Patroni class.

        Args:
            charm: PostgreSQL charm instance.
            unit_ip: IP address of the current unit
            cluster_name: name of the cluster
            member_name: name of the member inside the cluster
            planned_units: number of units planned for the cluster
            peers_ips: IP addresses of the peer units
            superuser_password: password for the operator user
            replication_password: password for the user used in the replication
            rewind_password: password for the user used on rewinds
            tls_enabled: whether TLS is enabled
        """
        self.charm = charm
        self.unit_ip = unit_ip
        self.cluster_name = cluster_name
        self.member_name = member_name
        self.planned_units = planned_units
        self.peers_ips = peers_ips
        self.superuser_password = superuser_password
        self.replication_password = replication_password
        self.rewind_password = rewind_password
        self.tls_enabled = tls_enabled
        # Variable mapping to requests library verify parameter.
        # The CA bundle file is used to validate the server certificate when
        # TLS is enabled, otherwise True is set because it's the default value.
        self.verify = f"{PATRONI_CONF_PATH}/{TLS_CA_FILE}" if tls_enabled else True

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
        self._change_owner(POSTGRESQL_DATA_PATH)

        # Create empty base config
        open(PG_BASE_CONF_PATH, "a").close()

        # Replicas refuse to start with the default permissions
        os.chmod(POSTGRESQL_DATA_PATH, 0o750)

    def _change_owner(self, path: str) -> None:
        """Change the ownership of a file or a directory to the postgres user.

        Args:
            path: path to a file or directory.
        """
        # Get the uid/gid for the snap_daemon user.
        user_database = pwd.getpwnam("snap_daemon")
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
        return {member["name"] for member in cluster_status.json()["members"]}

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

    def get_postgresql_version(self) -> str:
        """Return the PostgreSQL version from the system."""
        client = snap.SnapClient()
        for snp in client.get_installed_snaps():
            if snp["name"] == POSTGRESQL_SNAP_NAME:
                return snp["version"]

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

    def get_member_status(self, member_name: str) -> str:
        """Get cluster member status.

        Args:
            member_name: cluster member name.

        Returns:
            status of the cluster member or an empty string if the status
                couldn't be retrieved yet.
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
                        return member["state"]
        return ""

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

    def get_standby_leader(
        self, unit_name_pattern=False, check_whether_is_running: bool = False
    ) -> Optional[str]:
        """Get standby leader instance.

        Args:
            unit_name_pattern: whether to convert pod name to unit name
            check_whether_is_running: whether to check if the standby leader is running

        Returns:
            standby leader pod or unit name.
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
                    if member["role"] == "standby_leader":
                        if check_whether_is_running and member["state"] not in RUNNING_STATES:
                            logger.warning(f"standby leader {member['name']} is not running")
                            continue
                        standby_leader = member["name"]
                        if unit_name_pattern:
                            # Change the last dash to / in order to match unit name pattern.
                            standby_leader = "/".join(standby_leader.rsplit("-", 1))
                        return standby_leader

    def get_sync_standby_names(self) -> List[str]:
        """Get the list of sync standby unit names."""
        sync_standbys = []
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(2 * len(self.peers_ips) + 1)):
            with attempt:
                url = self._get_alternative_patroni_url(attempt)
                r = requests.get(f"{url}/cluster", verify=self.verify)
                for member in r.json()["members"]:
                    if member["role"] == "sync_standby":
                        sync_standbys.append("/".join(member["name"].rsplit("-", 1)))
        return sync_standbys

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

        # Check if all members are running and one of them is a leader (primary) or
        # a standby leader, because sometimes there may exist (for some period of time)
        # only replicas after a failed switchover.
        return all(
            member["state"] in RUNNING_STATES for member in cluster_status.json()["members"]
        ) and any(
            member["role"] in ["leader", "standby_leader"]
            for member in cluster_status.json()["members"]
        )

    def get_patroni_health(self) -> Dict[str, str]:
        """Gets, retires and parses the Patroni health endpoint."""
        for attempt in Retrying(stop=stop_after_delay(90), wait=wait_fixed(3)):
            with attempt:
                r = requests.get(
                    f"{self._patroni_url}/health",
                    verify=self.verify,
                    timeout=API_REQUEST_TIMEOUT,
                )

                return r.json()

    @property
    def is_creating_backup(self) -> bool:
        """Returns whether a backup is being created."""
        # Request info from cluster endpoint (which returns the list of tags from each
        # cluster member; the "is_creating_backup" tag means that the member is creating
        # a backup).
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(f"{self._patroni_url}/cluster", verify=self.verify)
        except RetryError:
            return False

        return any(
            "tags" in member and member["tags"].get("is_creating_backup")
            for member in r.json()["members"]
        )

    @property
    def is_replication_healthy(self) -> bool:
        """Return whether the replication is healthy."""
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    primary = self.get_primary()
                    primary_ip = self.get_member_ip(primary)
                    members_ips = {self.unit_ip}
                    members_ips.update(self.peers_ips)
                    for members_ip in members_ips:
                        endpoint = "leader" if members_ip == primary_ip else "replica?lag=16kB"
                        url = self._patroni_url.replace(self.unit_ip, members_ip)
                        member_status = requests.get(f"{url}/{endpoint}", verify=self.verify)
                        if member_status.status_code != 200:
                            raise Exception
        except RetryError:
            logger.exception("replication is not healthy")
            return False

        logger.debug("replication is healthy")
        return True

    @property
    def member_started(self) -> bool:
        """Has the member started Patroni and PostgreSQL.

        Returns:
            True if services is ready False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            response = self.get_patroni_health()
        except RetryError:
            return False

        return response["state"] in RUNNING_STATES

    @property
    def member_inactive(self) -> bool:
        """Are Patroni and PostgreSQL in inactive state.

        Returns:
            True if services is not running, starting or restarting. Retries over a period of 60
            seconds times to allow server time to start up.
        """
        try:
            response = self.get_patroni_health()
        except RetryError:
            return True

        return response["state"] not in [*RUNNING_STATES, "starting", "restarting"]

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
                return member.get("lag", "unknown")

        return "unknown"

    @property
    def is_member_isolated(self) -> bool:
        """Returns whether the unit is isolated from the cluster."""
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    cluster_status = requests.get(
                        f"{self._patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                        verify=self.verify,
                        timeout=API_REQUEST_TIMEOUT,
                    )
        except RetryError:
            # Return False if it was not possible to get the cluster info. Try again later.
            return False

        return len(cluster_status.json()["members"]) == 0

    def promote_standby_cluster(self) -> None:
        """Promote a standby cluster to be a regular cluster."""
        config_response = requests.get(f"{self._patroni_url}/config", verify=self.verify)
        if "standby_cluster" not in config_response.json():
            raise StandbyClusterAlreadyPromotedError("standby cluster is already promoted")
        requests.patch(
            f"{self._patroni_url}/config", verify=self.verify, json={"standby_cluster": None}
        )
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                if self.get_primary() is None:
                    raise ClusterNotPromotedError("cluster not promoted")

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

    def render_patroni_yml_file(
        self,
        connectivity: bool = False,
        is_creating_backup: bool = False,
        enable_tls: bool = False,
        stanza: str = None,
        restore_stanza: Optional[str] = None,
        backup_id: Optional[str] = None,
        parameters: Optional[dict[str, str]] = None,
    ) -> None:
        """Render the Patroni configuration file.

        Args:
            connectivity: whether to allow external connections to the database.
            is_creating_backup: whether this unit is creating a backup.
            enable_tls: whether to enable TLS.
            stanza: name of the stanza created by pgBackRest.
            restore_stanza: name of the stanza used when restoring a backup.
            backup_id: id of the backup that is being restored.
            parameters: PostgreSQL parameters to be added to the postgresql.conf file.
        """
        # Open the template patroni.yml file.
        with open("templates/patroni.yml.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            conf_path=PATRONI_CONF_PATH,
            connectivity=connectivity,
            is_creating_backup=is_creating_backup,
            log_path=PATRONI_LOGS_PATH,
            postgresql_log_path=POSTGRESQL_LOGS_PATH,
            data_path=POSTGRESQL_DATA_PATH,
            enable_tls=enable_tls,
            member_name=self.member_name,
            partner_addrs=self.charm.async_replication.get_partner_addresses(),
            peers_ips=self.peers_ips,
            pgbackrest_configuration_file=PGBACKREST_CONFIGURATION_FILE,
            scope=self.cluster_name,
            self_ip=self.unit_ip,
            superuser=USER,
            superuser_password=self.superuser_password,
            replication_password=self.replication_password,
            rewind_user=REWIND_USER,
            rewind_password=self.rewind_password,
            enable_pgbackrest=stanza is not None,
            restoring_backup=backup_id is not None,
            backup_id=backup_id,
            stanza=stanza,
            restore_stanza=restore_stanza,
            version=self.get_postgresql_version().split(".")[0],
            minority_count=self.planned_units // 2,
            pg_parameters=parameters,
            primary_cluster_endpoint=self.charm.async_replication.get_primary_cluster_endpoint(),
            extra_replication_endpoints=self.charm.async_replication.get_standby_endpoints(),
        )
        self.render_file(f"{PATRONI_CONF_PATH}/patroni.yaml", rendered, 0o600)

    def start_patroni(self) -> bool:
        """Start Patroni service using snap.

        Returns:
            Whether the service started successfully.
        """
        try:
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            selected_snap.start(services=["patroni"])
            return selected_snap.services["patroni"]["active"]
        except snap.SnapError as e:
            error_message = "Failed to start patroni snap service"
            logger.exception(error_message, exc_info=e)
            return False

    def stop_patroni(self) -> bool:
        """Stop Patroni service using systemd.

        Returns:
            Whether the service stopped successfully.
        """
        try:
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            selected_snap.stop(services=["patroni"])
            return not selected_snap.services["patroni"]["active"]
        except snap.SnapError as e:
            error_message = "Failed to stop patroni snap service"
            logger.exception(error_message, exc_info=e)
            return False

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
        raft_status = subprocess.check_output([
            "charmed-postgresql.syncobj-admin",
            "-conn",
            "127.0.0.1:2222",
            "-status",
        ]).decode("UTF-8")

        # Check whether the member is still part of the raft cluster.
        if not member_ip or member_ip not in raft_status:
            return

        # Remove the member from the raft cluster.
        result = subprocess.check_output([
            "charmed-postgresql.syncobj-admin",
            "-conn",
            "127.0.0.1:2222",
            "-remove",
            f"{member_ip}:2222",
        ]).decode("UTF-8")

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
        try:
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            selected_snap.restart(services=["patroni"])
            return selected_snap.services["patroni"]["active"]
        except snap.SnapError as e:
            error_message = "Failed to start patroni snap service"
            logger.exception(error_message, exc_info=e)
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def restart_postgresql(self) -> None:
        """Restart PostgreSQL."""
        requests.post(f"{self._patroni_url}/restart", verify=self.verify)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reinitialize_postgresql(self) -> None:
        """Reinitialize PostgreSQL."""
        requests.post(f"{self._patroni_url}/reinitialize", verify=self.verify)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def bulk_update_parameters_controller_by_patroni(self, parameters: Dict[str, Any]) -> None:
        """Update the value of a parameter controller by Patroni.

        For more information, check https://patroni.readthedocs.io/en/latest/patroni_configuration.html#postgresql-parameters-controlled-by-patroni.
        """
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self.verify,
            json={"postgresql": {"parameters": parameters}},
        )

    def update_synchronous_node_count(self, units: int = None) -> None:
        """Update synchronous_node_count to the minority of the planned cluster."""
        if units is None:
            units = self.planned_units
        # Try to update synchronous_node_count.
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                r = requests.patch(
                    f"{self._patroni_url}/config",
                    json={"synchronous_node_count": units // 2},
                    verify=self.verify,
                )

                # Check whether the update was unsuccessful.
                if r.status_code != 200:
                    raise UpdateSyncNodeCountError(f"received {r.status_code}")
