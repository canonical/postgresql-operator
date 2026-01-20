#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage cluster lifecycle."""

import glob
import json
import logging
import os
import pathlib
import pwd
import re
import shutil
import subprocess
from asyncio import as_completed, create_task, run, wait
from contextlib import suppress
from functools import cached_property
from pathlib import Path
from ssl import CERT_NONE, create_default_context
from typing import TYPE_CHECKING, Any, TypedDict

import psutil
import requests
import tomli
from charmlibs import snap
from httpx import AsyncClient, BasicAuth, HTTPError
from jinja2 import Template
from ops import BlockedStatus
from pysyncobj.utility import TcpUtility, UtilityException
from requests.auth import HTTPBasicAuth
from single_kernel_postgresql.config.literals import (
    PEER,
    POSTGRESQL_STORAGE_PERMISSIONS,
    REWIND_USER,
    USER,
)
from tenacity import (
    Future,
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
    PATRONI_SERVICE_DEFAULT_PATH,
    PGBACKREST_CONFIGURATION_FILE,
    POSTGRESQL_CONF_PATH,
    POSTGRESQL_DATA_PATH,
    POSTGRESQL_LOGS_PATH,
    TLS_CA_BUNDLE_FILE,
)
from utils import label2name

logger = logging.getLogger(__name__)

PG_BASE_CONF_PATH = f"{POSTGRESQL_CONF_PATH}/postgresql.conf"

STARTED_STATES = ["running", "streaming"]
RUNNING_STATES = [*STARTED_STATES, "starting"]

PATRONI_TIMEOUT = 10

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


class RaftPostgresqlNotUpError(Exception):
    """Postgresql not yet started."""


class RaftPostgresqlStillUpError(Exception):
    """Postgresql not yet down."""


class RaftNotPromotedError(Exception):
    """Leader not yet set when reinitialising raft."""


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


class SwitchoverNotSyncError(SwitchoverFailedError):
    """Raised when a switchover failed because node is not sync."""


class UpdateSyncNodeCountError(Exception):
    """Raised when updating synchronous_node_count failed for some reason."""


class ClusterMember(TypedDict):
    """Type for cluster member."""

    name: str
    role: str
    state: str
    api_url: str
    host: str
    port: int
    timeline: int
    lag: int


class Patroni:
    """This class handles the bootstrap of a PostgreSQL database through Patroni."""

    def __init__(
        self,
        charm: "PostgresqlOperatorCharm",
        unit_ip: str | None,
        cluster_name: str,
        member_name: str,
        planned_units: int,
        peers_ips: set[str],
        superuser_password: str | None,
        replication_password: str | None,
        rewind_password: str | None,
        raft_password: str | None,
        patroni_password: str | None,
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
            raft_password: password for raft
            patroni_password: password for the user used on patroni
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
        self.raft_password = raft_password
        self.patroni_password = patroni_password
        # Variable mapping to requests library verify parameter.
        # The CA bundle file is used to validate the server certificate when
        # TLS is enabled, otherwise True is set because it's the default value.
        self.verify = f"{PATRONI_CONF_PATH}/{TLS_CA_BUNDLE_FILE}"

    @property
    def _are_passwords_set(self) -> bool:
        return all([
            self.superuser_password,
            self.replication_password,
            self.rewind_password,
            self.raft_password,
            self.patroni_password,
        ])

    @cached_property
    def _patroni_auth(self) -> HTTPBasicAuth | None:
        if self.patroni_password:
            return HTTPBasicAuth("patroni", self.patroni_password)

    @cached_property
    def _patroni_async_auth(self) -> BasicAuth | None:
        if self.patroni_password:
            return BasicAuth("patroni", password=self.patroni_password)

    @cached_property
    def _patroni_url(self) -> str:
        """Patroni REST API URL."""
        return f"https://{self.unit_ip}:8008"

    @staticmethod
    def _dict_to_hba_string(_dict: dict[str, Any]) -> str:
        """Transform a dictionary into a Host Based Authentication valid string."""
        for key, value in _dict.items():
            if isinstance(value, bool):
                _dict[key] = int(value)
            if isinstance(value, str):
                _dict[key] = f'"{value}"'

        return " ".join(f"{key}={value}" for key, value in _dict.items())

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

        # Expected permission
        # Replicas refuse to start with the default permissions
        os.chmod(POSTGRESQL_DATA_PATH, POSTGRESQL_STORAGE_PERMISSIONS)

    def _change_owner(self, path: str) -> None:
        """Change the ownership of a file or a directory to the postgres user.

        Args:
            path: path to a file or directory.
        """
        # Get the uid/gid for the _daemon_ user.
        user_database = pwd.getpwnam("_daemon_")
        # Set the correct ownership for the file or directory.
        os.chown(path, uid=user_database.pw_uid, gid=user_database.pw_gid)

    @cached_property
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        return {member["name"] for member in self.cached_cluster_status}

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
        with pathlib.Path("refresh_versions.toml").open("rb") as file:
            return tomli.load(file)["workload"]

    @cached_property
    def cached_cluster_status(self):
        """Cached cluster status."""
        return self.cluster_status()

    def cluster_status(self, alternative_endpoints: list | None = None) -> list[ClusterMember]:
        """Query the cluster status."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        if response := self.parallel_patroni_get_request(
            f"/{PATRONI_CLUSTER_STATUS_ENDPOINT}", alternative_endpoints
        ):
            logger.debug("API cluster_status: %s", response["members"])
            return response["members"]
        raise RetryError(
            last_attempt=Future.construct(1, Exception("Unable to reach any units"), True)
        )

    def get_member_ip(self, member_name: str) -> str | None:
        """Get cluster member IP address.

        Args:
            member_name: cluster member name.

        Returns:
            IP address of the cluster member.
        """
        try:
            cluster_status = self.cluster_status()

            for member in cluster_status:
                if member["name"] == member_name:
                    return member["host"]
        except RetryError:
            logger.debug("Unable to get IP. Cluster status unreachable")

    def get_member_status(self, member_name: str) -> str:
        """Get cluster member status.

        Args:
            member_name: cluster member name.

        Returns:
            status of the cluster member or an empty string if the status
                couldn't be retrieved yet.
        """
        # Request info from cluster endpoint (which returns all members of the cluster).
        cluster_status = self.cluster_status()
        if cluster_status:
            for member in cluster_status:
                if member["name"] == member_name:
                    return member["state"]
        return ""

    async def _httpx_get_request(self, url: str, verify: bool = True) -> dict[str, Any] | None:
        if not self._patroni_async_auth:
            return None
        ssl_ctx = create_default_context()
        if verify:
            with suppress(FileNotFoundError):
                ssl_ctx.load_verify_locations(cafile=f"{PATRONI_CONF_PATH}/{TLS_CA_BUNDLE_FILE}")
        else:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = CERT_NONE
        async with AsyncClient(
            auth=self._patroni_async_auth, timeout=API_REQUEST_TIMEOUT, verify=ssl_ctx
        ) as client:
            try:
                return (await client.get(url)).json()
            except (HTTPError, ValueError):
                return None

    async def _async_get_request(
        self, uri: str, endpoints: list[str], verify: bool = True
    ) -> dict[str, Any] | None:
        tasks = [
            create_task(self._httpx_get_request(f"https://{ip}:8008{uri}", verify))
            for ip in endpoints
        ]
        for task in as_completed(tasks):
            if result := await task:
                for task in tasks:
                    task.cancel()
                await wait(tasks)
                return result

    def parallel_patroni_get_request(
        self, uri: str, endpoints: list[str] | None = None
    ) -> dict[str, Any] | None:
        """Call all possible patroni endpoints in parallel."""
        if not endpoints:
            endpoints = []
            if self.unit_ip:
                endpoints.append(self.unit_ip)
            for peer_ip in self.peers_ips:
                endpoints.append(peer_ip)
            verify = True
        else:
            # TODO we don't know the other cluster's ca
            verify = False
        return run(self._async_get_request(uri, endpoints, verify))

    def get_primary(
        self, unit_name_pattern=False, alternative_endpoints: list[str] | None = None
    ) -> str | None:
        """Get primary instance.

        Args:
            unit_name_pattern: whether to convert pod name to unit name
            alternative_endpoints: list of alternative endpoints to check for the primary.

        Returns:
            primary pod or unit name.
        """
        # Request info from cluster endpoint (which returns all members of the cluster).
        try:
            cluster_status = self.cluster_status(alternative_endpoints)
            for member in cluster_status:
                if member["role"] == "leader":
                    primary = member["name"]
                    if unit_name_pattern:
                        # Change the last dash to / in order to match unit name pattern.
                        primary = label2name(primary)
                    return primary
        except RetryError:
            logger.debug("Unable to get primary. Cluster status unreachable")

    def get_standby_leader(
        self, unit_name_pattern=False, check_whether_is_running: bool = False
    ) -> str | None:
        """Get standby leader instance.

        Args:
            unit_name_pattern: whether to convert pod name to unit name
            check_whether_is_running: whether to check if the standby leader is running

        Returns:
            standby leader pod or unit name.
        """
        # Request info from cluster endpoint (which returns all members of the cluster).
        cluster_status = self.cluster_status()
        if cluster_status:
            for member in cluster_status:
                if member["role"] == "standby_leader":
                    if check_whether_is_running and member["state"] not in STARTED_STATES:
                        logger.warning(f"standby leader {member['name']} is not running")
                        continue
                    standby_leader = member["name"]
                    if unit_name_pattern:
                        # Change the last dash to / in order to match unit name pattern.
                        standby_leader = label2name(standby_leader)
                    return standby_leader

    def get_sync_standby_names(self) -> list[str]:
        """Get the list of sync standby unit names."""
        sync_standbys = []
        # Request info from cluster endpoint (which returns all members of the cluster).
        cluster_status = self.cluster_status()
        if cluster_status:
            for member in cluster_status:
                if member["role"] == "sync_standby":
                    sync_standbys.append(label2name(member["name"]))
        return sync_standbys

    def are_all_members_ready(self) -> bool:
        """Check if all members are correctly running Patroni and PostgreSQL.

        Returns:
            True if all members are ready False otherwise. Retries over a period of 10 seconds
            3 times to allow server time to start up.
        """
        # Request info from cluster endpoint
        # (which returns all members of the cluster and their states).
        try:
            members = self.cluster_status()
        except RetryError:
            return False

        # Check if all members are running and one of them is a leader (primary) or
        # a standby leader, because sometimes there may exist (for some period of time)
        # only replicas after a failed switchover.
        return all(member["state"] in STARTED_STATES for member in members) and any(
            member["role"] in ["leader", "standby_leader"] for member in members
        )

    @cached_property
    def cached_patroni_health(self) -> dict[str, str]:
        """Cached local unit health."""
        return self.get_patroni_health()

    def get_patroni_health(self) -> dict[str, str]:
        """Gets, retires and parses the Patroni health endpoint."""
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(7)):
            with attempt:
                r = requests.get(
                    f"{self._patroni_url}/health",
                    verify=self.verify,
                    timeout=API_REQUEST_TIMEOUT,
                    auth=self._patroni_auth,
                )
                logger.debug("API get_patroni_health: %s (%s)", r, r.elapsed.total_seconds())

        return r.json()

    @property
    def is_creating_backup(self) -> bool:
        """Returns whether a backup is being created."""
        # Request info from cluster endpoint (which returns the list of tags from each
        # cluster member; the "is_creating_backup" tag means that the member is creating
        # a backup).
        try:
            members = self.cached_cluster_status
        except RetryError:
            return False

        return any(
            "tags" in member and member["tags"].get("is_creating_backup") for member in members
        )

    def is_replication_healthy(self) -> bool:
        """Return whether the replication is healthy."""
        if not self.unit_ip:
            logger.debug("Failed replication check no IP set")
            return False
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    primary = self.get_primary()
                    if not primary:
                        logger.debug("Failed replication check no primary reported")
                        raise Exception
                    primary_ip = self.get_member_ip(primary)
                    members_ips = {self.unit_ip}
                    members_ips.update(self.peers_ips)
                    for members_ip in members_ips:
                        endpoint = "leader" if members_ip == primary_ip else "replica?lag=16kB"
                        url = self._patroni_url.replace(self.unit_ip, members_ip)
                        r = requests.get(
                            f"{url}/{endpoint}",
                            verify=self.verify,
                            auth=self._patroni_auth,
                            timeout=PATRONI_TIMEOUT,
                        )
                        logger.debug(
                            "API is_replication_healthy: %s (%s)",
                            r,
                            r.elapsed.total_seconds(),
                        )
                        if r.status_code != 200:
                            logger.debug(
                                f"Failed replication check for {members_ip} with code {r.status_code}"
                            )
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
        if not self.is_patroni_running():
            return False
        try:
            response = self.cached_patroni_health
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
            response = self.cached_patroni_health
        except RetryError:
            return True

        return response["state"] not in [
            *RUNNING_STATES,
            "creating replica",
            "starting",
            "restarting",
        ]

    @property
    def is_member_isolated(self) -> bool:
        """Returns whether the unit is isolated from the cluster."""
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(
                        f"{self._patroni_url}/{PATRONI_CLUSTER_STATUS_ENDPOINT}",
                        verify=self.verify,
                        timeout=API_REQUEST_TIMEOUT,
                        auth=self._patroni_auth,
                    )
                    logger.debug(
                        "API is_member_isolated: %s (%s)",
                        r.json()["members"],
                        r.elapsed.total_seconds(),
                    )
        except RetryError:
            # Return False if it was not possible to get the cluster info. Try again later.
            return False

        return len(r.json()["members"]) == 0

    def online_cluster_members(self) -> list[ClusterMember]:
        """Return list of online cluster members."""
        try:
            cluster_status = self.cluster_status()
        except RetryError:
            logger.exception("Unable to get the state of the cluster")
            return []
        if not cluster_status:
            return []

        return [member for member in cluster_status if member["state"] in STARTED_STATES]

    def are_replicas_up(self) -> dict[str, bool] | None:
        """Check if cluster members are running or streaming."""
        try:
            members = self.cluster_status()
            return {member["host"]: member["state"] in STARTED_STATES for member in members}
        except Exception:
            logger.exception("Unable to get the state of the cluster")
            return

    def promote_standby_cluster(self) -> None:
        """Promote a standby cluster to be a regular cluster."""
        config_response = requests.get(
            f"{self._patroni_url}/config",
            verify=self.verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug(
            "API promote_standby_cluster: %s (%s)",
            config_response,
            config_response.elapsed.total_seconds(),
        )
        if "standby_cluster" not in config_response.json():
            raise StandbyClusterAlreadyPromotedError("standby cluster is already promoted")
        r = requests.patch(
            f"{self._patroni_url}/config",
            verify=self.verify,
            json={"standby_cluster": None},
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug("API promote_standby_cluster patch: %s (%s)", r, r.elapsed.total_seconds())
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                if self.get_primary() is None:
                    raise ClusterNotPromotedError("cluster not promoted")

    def render_file(self, path: str, content: str, mode: int, change_owner: bool = True) -> None:
        """Write a content rendered from a template to a file.

        Args:
            path: the path to the file.
            content: the data to be written to the file.
            mode: access permission mask applied to the
              file using chmod (e.g. 0o640).
            change_owner: whether to change the file owner
              to the _daemon_ user.
        """
        # TODO: keep this method to use it also for generating replication configuration files and
        # move it to an utils / helpers file.
        # Write the content to the file.
        with open(path, "w+") as file:
            file.write(content)
        # Ensure correct permissions are set on the file.
        os.chmod(path, mode)
        if change_owner:
            self._change_owner(path)

    def render_patroni_yml_file(
        self,
        connectivity: bool = False,
        is_creating_backup: bool = False,
        enable_ldap: bool = False,
        enable_tls: bool = False,
        stanza: str | None = None,
        restore_stanza: str | None = None,
        disable_pgbackrest_archiving: bool = False,
        backup_id: str | None = None,
        pitr_target: str | None = None,
        restore_timeline: str | None = None,
        restore_to_latest: bool = False,
        parameters: dict[str, str] | None = None,
        no_peers: bool = False,
        user_databases_map: dict[str, str] | None = None,
        slots: dict[str, str] | None = None,
    ) -> None:
        """Render the Patroni configuration file.

        Args:
            connectivity: whether to allow external connections to the database.
            is_creating_backup: whether this unit is creating a backup.
            enable_ldap: whether to enable LDAP authentication.
            enable_tls: whether to enable client TLS.
            stanza: name of the stanza created by pgBackRest.
            restore_stanza: name of the stanza used when restoring a backup.
            disable_pgbackrest_archiving: whether to force disable pgBackRest WAL archiving.
            backup_id: id of the backup that is being restored.
            pitr_target: point-in-time-recovery target for the restore.
            restore_timeline: timeline to restore from.
            restore_to_latest: restore all the WAL transaction logs from the stanza.
            parameters: PostgreSQL parameters to be added to the postgresql.conf file.
            no_peers: Don't include peers.
            user_databases_map: map of databases to be accessible by each user.
            slots: replication slots (keys) with assigned database name (values).
        """
        slots = slots or {}
        if not self._are_passwords_set:
            logger.warning("Passwords are not yet generated by the leader")
            return

        # Open the template patroni.yml file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())

        ldap_params = self.charm.get_ldap_parameters()

        # Render the template file with the correct values.
        rendered = template.render(
            conf_path=PATRONI_CONF_PATH,
            connectivity=connectivity,
            is_creating_backup=is_creating_backup,
            log_path=PATRONI_LOGS_PATH,
            postgresql_log_path=POSTGRESQL_LOGS_PATH,
            data_path=POSTGRESQL_DATA_PATH,
            enable_ldap=enable_ldap,
            enable_tls=enable_tls,
            member_name=self.member_name,
            partner_addrs=self.charm.async_replication.get_partner_addresses()
            if not no_peers
            else [],
            peers_ips=sorted(self.peers_ips) if not no_peers else set(),
            pgbackrest_configuration_file=PGBACKREST_CONFIGURATION_FILE,
            scope=self.cluster_name,
            self_ip=self.unit_ip,
            listen_ips=self.charm.listen_ips,
            superuser=USER,
            superuser_password=self.superuser_password,
            replication_password=self.replication_password,
            rewind_user=REWIND_USER,
            rewind_password=self.rewind_password,
            enable_pgbackrest_archiving=stanza is not None
            and disable_pgbackrest_archiving is False,
            restoring_backup=backup_id is not None or pitr_target is not None,
            backup_id=backup_id,
            pitr_target=pitr_target if not restore_to_latest else None,
            restore_timeline=restore_timeline,
            restore_to_latest=restore_to_latest,
            stanza=stanza,
            restore_stanza=restore_stanza,
            version=self.get_postgresql_version().split(".")[0],
            synchronous_node_count=self._synchronous_node_count,
            pg_parameters=parameters,
            primary_cluster_endpoint=self.charm.async_replication.get_primary_cluster_endpoint(),
            extra_replication_endpoints=self.charm.async_replication.get_standby_endpoints(),
            raft_password=self.raft_password,
            ldap_parameters=self._dict_to_hba_string(ldap_params),
            patroni_password=self.patroni_password,
            user_databases_map=user_databases_map,
            slots=slots,
            instance_password_encryption=self.charm.config.instance_password_encryption,
        )
        self.render_file(f"{PATRONI_CONF_PATH}/patroni.yaml", rendered, 0o600)

    def start_patroni(self) -> bool:
        """Start Patroni service using snap.

        Returns:
            Whether the service started successfully.
        """
        try:
            logger.debug("Starting Patroni...")
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            selected_snap.start(services=["patroni"])
            return selected_snap.services["patroni"]["active"]
        except snap.SnapError as e:
            error_message = "Failed to start patroni snap service"
            logger.exception(error_message, exc_info=e)
            return False

    def patroni_logs(self, num_lines: int | str | None = 10) -> str:
        """Get Patroni snap service logs. Executes only on current unit.

        Args:
            num_lines: number of log last lines being returned.

        Returns:
            Multi-line logs string.
        """
        try:
            logger.debug("Getting Patroni logs...")
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            # Lib definition of num_lines only allows int
            return selected_snap.logs(services=["patroni"], num_lines=num_lines)  # pyright: ignore
        except snap.SnapError as e:
            error_message = "Failed to get logs from patroni snap service"
            logger.exception(error_message, exc_info=e)
            return ""

    def last_postgresql_logs(self) -> str:
        """Get last log file content of Postgresql service.

        If there is no available log files, empty line will be returned.

        Returns:
            Content of last log file of Postgresql service.
        """
        log_files = glob.glob(f"{POSTGRESQL_LOGS_PATH}/*.log")
        if len(log_files) == 0:
            return ""
        log_files.sort(reverse=True)
        try:
            with open(log_files[0]) as last_log_file:
                return last_log_file.read()
        except OSError as e:
            error_message = "Failed to read last postgresql log file"
            logger.exception(error_message, exc_info=e)
            return ""

    def stop_patroni(self) -> bool:
        """Stop Patroni service using systemd.

        Returns:
            Whether the service stopped successfully.
        """
        try:
            logger.debug("Stopping Patroni...")
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            selected_snap.stop(services=["patroni"])
            return not selected_snap.services["patroni"]["active"]
        except snap.SnapError as e:
            error_message = "Failed to stop patroni snap service"
            logger.exception(error_message, exc_info=e)
            return False

    def switchover(self, candidate: str | None = None, async_cluster: bool = False) -> None:
        """Trigger a switchover."""
        # Try to trigger the switchover.
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                current_primary = (
                    self.get_primary() if not async_cluster else self.get_standby_leader()
                )
                if current_primary == candidate:
                    logger.info("Candidate and leader are the same")
                    return

                body = {"leader": current_primary}
                if candidate:
                    body["candidate"] = candidate
                r = requests.post(
                    f"{self._patroni_url}/switchover",
                    json=body,
                    verify=self.verify,
                    auth=self._patroni_auth,
                    timeout=PATRONI_TIMEOUT,
                )
                logger.debug("API switchover: %s (%s)", r, r.elapsed.total_seconds())

        # Check whether the switchover was unsuccessful.
        if r.status_code != 200:
            if (
                r.status_code == 412
                and r.text == "candidate name does not match with sync_standby"
            ):
                logger.debug("Unit is not sync standby")
                raise SwitchoverNotSyncError()
            logger.warning(f"Switchover call failed with code {r.status_code} {r.text}")
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

    def has_raft_quorum(self) -> bool:
        """Check if raft cluster has quorum."""
        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=self.raft_password, timeout=3)

        raft_host = "127.0.0.1:2222"
        try:
            raft_status = syncobj_util.executeCommand(raft_host, ["status"])
        except UtilityException:
            logger.warning("Has raft quorum: Cannot connect to raft cluster")
            return False
        if not raft_status:
            logger.warning("Has raft quorum: No status reported")
            return False
        return raft_status["has_quorum"]

    def remove_raft_data(self) -> None:
        """Stops Patroni and removes the raft journals."""
        logger.info("Stopping patroni")
        self.stop_patroni()

        logger.info("Wait for postgresql to stop")
        for attempt in Retrying(wait=wait_fixed(5)):
            with attempt:
                for proc in psutil.process_iter(["name"]):
                    if proc.name() == "postgres":
                        raise RaftPostgresqlStillUpError()

        logger.info("Removing raft data")
        try:
            path = Path(f"{PATRONI_CONF_PATH}/raft")
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
        except OSError as e:
            raise Exception(
                f"Failed to remove previous cluster information with error: {e!s}"
            ) from e
        logger.info("Raft ready to reinitialise")

    def reinitialise_raft_data(self) -> None:
        """Reinitialise the raft journals and promoting the unit to leader. Should only be run on sync replicas."""
        logger.info("Rerendering patroni config without peers")
        self.charm.update_config(no_peers=True)
        logger.info("Starting patroni")
        self.start_patroni()

        logger.info("Waiting for new raft cluster to initialise")
        for attempt in Retrying(wait=wait_fixed(5)):
            with attempt:
                health_status = self.get_patroni_health()
                if (
                    health_status["role"] not in ["leader", "master"]
                    or health_status["state"] != "running"
                ):
                    raise RaftNotPromotedError()

        logger.info("Restarting patroni")
        self.restart_patroni()
        for attempt in Retrying(wait=wait_fixed(5)):
            with attempt:
                found_postgres = False
                for proc in psutil.process_iter(["name"]):
                    if proc.name() == "postgres":
                        found_postgres = True
                        break
                if not found_postgres:
                    raise RaftPostgresqlNotUpError()
        logger.info("Raft should be unstuck")

    def get_running_cluster_members(self) -> list[str]:
        """List running patroni members."""
        try:
            members = self.cluster_status()
            return [member["name"] for member in members if member["state"] in STARTED_STATES]
        except Exception:
            return []

    def remove_raft_member(self, member_ip: str) -> None:
        """Remove a member from the raft cluster.

        The raft cluster is a different cluster from the Patroni cluster.
        It is responsible for defining which Patroni member can update
        the primary member in the DCS.

        Raises:
            RaftMemberNotFoundError: if the member to be removed
                is not part of the raft cluster.
        """
        if self.charm.has_raft_keys():
            logger.debug("Remove raft member: Raft already in recovery")
            return

        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=self.raft_password, timeout=3)

        raft_host = "127.0.0.1:2222"
        try:
            raft_status = syncobj_util.executeCommand(raft_host, ["status"])
        except UtilityException:
            logger.warning("Remove raft member: Cannot connect to raft cluster")
            raise RemoveRaftMemberFailedError() from None
        if not raft_status:
            logger.warning("Remove raft member: No raft status")
            raise RemoveRaftMemberFailedError() from None

        # Check whether the member is still part of the raft cluster.
        if not member_ip or f"partner_node_status_server_{member_ip}:2222" not in raft_status:
            return

        # If there's no quorum and the leader left raft cluster is stuck
        if not raft_status["has_quorum"] and (
            not raft_status["leader"] or raft_status["leader"].host == member_ip
        ):
            self.charm.set_unit_status(
                BlockedStatus("Raft majority loss, run: promote-to-primary")
            )
            logger.warning("Remove raft member: Stuck raft cluster detected")
            data_flags = {"raft_stuck": "True"}
            self.charm.unit_peer_data.update(data_flags)

            # Leader doesn't always trigger when changing it's own peer data.
            if self.charm.unit.is_leader():
                self.charm.on[PEER].relation_changed.emit(
                    unit=self.charm.unit,
                    app=self.charm.app,
                    relation=self.charm.model.get_relation(PEER),
                )
            return

        # Suppressing since the call will be removed soon
        # Remove the member from the raft cluster.
        try:
            result = syncobj_util.executeCommand(raft_host, ["remove", f"{member_ip}:2222"])
        except UtilityException:
            logger.debug("Remove raft member: Remove call failed")
            raise RemoveRaftMemberFailedError() from None

        if not result or not result.startswith("SUCCESS"):
            logger.debug(f"Remove raft member: Remove call not successful with {result}")
            raise RemoveRaftMemberFailedError()

    @retry(stop=stop_after_attempt(20), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reload_patroni_configuration(self):
        """Reload Patroni configuration after it was changed."""
        logger.debug("Reloading Patroni configuration...")
        r = requests.post(
            f"{self._patroni_url}/reload",
            verify=self.verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug("API reload_patroni_configuration: %s (%s)", r, r.elapsed.total_seconds())

    def is_patroni_running(self) -> bool:
        """Check if the Patroni service is running."""
        try:
            cache = snap.SnapCache()
            selected_snap = cache["charmed-postgresql"]
            return selected_snap.services["patroni"]["active"]
        except snap.SnapError as e:
            logger.debug(f"Failed to check Patroni service: {e}")
            return False

    def restart_patroni(self) -> bool:
        """Restart Patroni.

        Returns:
            Whether the service restarted successfully.
        """
        try:
            logger.debug("Restarting Patroni...")
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
        logger.debug("Restarting PostgreSQL...")
        r = requests.post(
            f"{self._patroni_url}/restart",
            verify=self.verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug("API restart_postgresql: %s (%s)", r, r.elapsed.total_seconds())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reinitialize_postgresql(self) -> None:
        """Reinitialize PostgreSQL."""
        logger.debug("Reinitializing PostgreSQL...")
        r = requests.post(
            f"{self._patroni_url}/reinitialize",
            verify=self.verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug("API reinitialize_postgresql: %s (%s)", r, r.elapsed.total_seconds())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def bulk_update_parameters_controller_by_patroni(
        self, parameters: dict[str, Any], base_parameters: dict[str, Any] | None
    ) -> None:
        """Update the value of a parameter controller by Patroni.

        For more information, check https://patroni.readthedocs.io/en/latest/patroni_configuration.html#postgresql-parameters-controlled-by-patroni.
        """
        if not base_parameters:
            base_parameters = {}
        r = requests.patch(
            f"{self._patroni_url}/config",
            verify=self.verify,
            json={
                "postgresql": {
                    "remove_data_directory_on_rewind_failure": False,
                    "remove_data_directory_on_diverged_timelines": False,
                    "parameters": parameters,
                },
                **base_parameters,
            },
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug(
            "API bulk_update_parameters_controller_by_patroni: %s (%s)",
            r,
            r.elapsed.total_seconds(),
        )

    def ensure_slots_controller_by_patroni(self, slots: dict[str, str]) -> None:
        """Synchronises slots controlled by Patroni with the provided state by removing unneeded slots and creating new ones.

        Args:
            slots: dictionary of slots in the {slot: database} format.
        """
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                current_config = requests.get(
                    f"{self._patroni_url}/config",
                    verify=self.verify,
                    timeout=PATRONI_TIMEOUT,
                    auth=self._patroni_auth,
                )
                logger.debug(
                    "API ensure_slots_controller_by_patroni: %s (%s)",
                    current_config,
                    current_config.elapsed.total_seconds(),
                )
                if current_config.status_code != 200:
                    raise Exception(
                        f"Failed to get current Patroni config: {current_config.status_code} {current_config.text}"
                    )
        slots_patch: dict[str, dict[str, str] | None] = dict.fromkeys(
            current_config.json().get("slots") or {}
        )
        for slot, database in slots.items():
            slots_patch[slot] = {
                "database": database,
                "plugin": "pgoutput",
                "type": "logical",
            }
        r = requests.patch(
            f"{self._patroni_url}/config",
            verify=self.verify,
            json={"slots": slots_patch},
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        logger.debug(
            "API ensure_slots_controller_by_patroni: %s (%s)",
            r,
            r.elapsed.total_seconds(),
        )

    @cached_property
    def _synchronous_node_count(self) -> int:
        planned_units = self.charm.app.planned_units()
        if self.charm.config.synchronous_node_count == "all":
            return planned_units - 1
        elif self.charm.config.synchronous_node_count == "majority":
            return planned_units // 2
        # -1 for leader
        return (
            self.charm.config.synchronous_node_count
            if self.charm.config.synchronous_node_count < planned_units - 1
            else planned_units - 1
        )

    @cached_property
    def synchronous_configuration(self) -> dict[str, Any]:
        """Synchronous mode configuration."""
        # Try to update synchronous_node_count.
        member_units = json.loads(self.charm.app_peer_data.get("members_ips", "[]"))
        return {
            "synchronous_node_count": self._synchronous_node_count,
            "synchronous_mode_strict": len(member_units) > 1
            # Explicitly setting 0 is to disable sync mode
            and self.charm.config.synchronous_node_count != 0
            and self._synchronous_node_count > 0,
        }

    def update_synchronous_node_count(self) -> None:
        """Update synchronous_node_count to the minority of the planned cluster."""
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                r = requests.patch(
                    f"{self._patroni_url}/config",
                    json=self.synchronous_configuration,
                    verify=self.verify,
                    auth=self._patroni_auth,
                    timeout=PATRONI_TIMEOUT,
                )
                logger.debug(
                    "API update_synchronous_node_count: %s (%s)", r, r.elapsed.total_seconds()
                )

                # Check whether the update was unsuccessful.
                if r.status_code != 200:
                    raise UpdateSyncNodeCountError(f"received {r.status_code}")

    def get_patroni_restart_condition(self) -> str:
        """Get current restart condition for Patroni systemd service. Executes only on current unit.

        Returns:
            Patroni systemd service restart condition.
        """
        with open(PATRONI_SERVICE_DEFAULT_PATH) as patroni_service_file:
            patroni_service = patroni_service_file.read()
            found_restart = re.findall(r"Restart=(\w+)", patroni_service)
            if len(found_restart) == 1:
                return str(found_restart[0])
        raise RuntimeError("failed to find patroni service restart condition")

    def update_patroni_restart_condition(self, new_condition: str) -> None:
        """Override restart condition for Patroni systemd service by rewriting service file and doing daemon-reload.

        Executes only on current unit.

        Args:
            new_condition: new Patroni systemd service restart condition.
        """
        logger.info(f"setting restart-condition to {new_condition} for patroni service")
        with open(PATRONI_SERVICE_DEFAULT_PATH) as patroni_service_file:
            patroni_service = patroni_service_file.read()
        logger.debug(f"patroni service file: {patroni_service}")
        new_patroni_service = re.sub(r"Restart=\w+", f"Restart={new_condition}", patroni_service)
        logger.debug(f"new patroni service file: {new_patroni_service}")
        with open(PATRONI_SERVICE_DEFAULT_PATH, "w") as patroni_service_file:
            patroni_service_file.write(new_patroni_service)
        subprocess.run(["/bin/systemctl", "daemon-reload"])
