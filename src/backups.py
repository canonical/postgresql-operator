# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Backups implementation."""

import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from functools import cached_property
from io import BytesIO
from pathlib import Path
from subprocess import TimeoutExpired, run

from boto3.session import Session
from botocore.client import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ParamValidationError, SSLError
from botocore.loaders import create_loader
from botocore.regions import EndpointResolver
from charmlibs import snap
from charms.data_platform_libs.v0.s3 import CredentialsChangedEvent, S3Requirer
from jinja2 import Template
from ops.charm import ActionEvent, HookEvent
from ops.framework import Object
from ops.model import ActiveStatus, MaintenanceStatus
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from constants import (
    BACKUP_ID_FORMAT,
    BACKUP_TYPE_OVERRIDES,
    BACKUP_USER,
    PATRONI_CONF_PATH,
    PGBACKREST_ARCHIVE_TIMEOUT_ERROR_CODE,
    PGBACKREST_BACKUP_ID_FORMAT,
    PGBACKREST_CONF_PATH,
    PGBACKREST_CONFIGURATION_FILE,
    PGBACKREST_EXECUTABLE,
    PGBACKREST_LOG_LEVEL_STDERR,
    PGBACKREST_LOGROTATE_FILE,
    PGBACKREST_LOGS_PATH,
    POSTGRESQL_DATA_PATH,
    UNIT_SCOPE,
)
from relations.async_replication import REPLICATION_CONSUMER_RELATION, REPLICATION_OFFER_RELATION

logger = logging.getLogger(__name__)

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"

S3_BLOCK_MESSAGES = [
    ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
    FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE,
    FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
]


class ListBackupsError(Exception):
    """Raised when pgBackRest fails to list backups."""


class PostgreSQLBackups(Object):
    """In this class, we manage PostgreSQL backups."""

    def __init__(self, charm, relation_name: str):
        """Manager of PostgreSQL backups."""
        super().__init__(charm, "backup")
        self.charm = charm
        self.relation_name = relation_name

        # s3 relation handles the config options for s3 backups
        self.s3_client = S3Requirer(self.charm, self.relation_name)
        self.framework.observe(
            self.s3_client.on.credentials_changed, self._on_s3_credential_changed
        )
        # When the leader unit is being removed, s3_client.on.credentials_gone is performed on it (and only on it).
        # After a new leader is elected, the S3 connection must be reinitialized.
        self.framework.observe(self.charm.on.leader_elected, self._on_s3_credential_changed)
        self.framework.observe(self.s3_client.on.credentials_gone, self._on_s3_credential_gone)
        self.framework.observe(self.charm.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.charm.on.list_backups_action, self._on_list_backups_action)
        self.framework.observe(self.charm.on.restore_action, self._on_restore_action)

    @cached_property
    def stanza_name(self) -> str:
        """Stanza name, composed by model and cluster name."""
        return f"{self.model.name}.{self.charm.cluster_name}"

    @property
    def _tls_ca_chain_filename(self) -> str:
        """Returns the path to the TLS CA chain file."""
        s3_parameters, _ = self._retrieve_s3_parameters()
        if s3_parameters.get("tls-ca-chain") is not None:
            return f"{PGBACKREST_CONF_PATH}/pgbackrest-tls-ca-chain.crt"
        return ""

    def _get_s3_session_resource(self, s3_parameters: dict):
        kwargs = {
            "aws_access_key_id": s3_parameters["access-key"],
            "aws_secret_access_key": s3_parameters["secret-key"],
        }
        if "region" in s3_parameters:
            kwargs["region_name"] = s3_parameters["region"]
        session = Session(**kwargs)
        return session.resource(
            "s3",
            endpoint_url=self._construct_endpoint(s3_parameters),
            verify=(self._tls_ca_chain_filename or None),
            config=Config(
                # https://github.com/boto/boto3/issues/4400#issuecomment-2600742103
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def _are_backup_settings_ok(self) -> tuple[bool, str]:
        """Validates whether backup settings are OK."""
        if self.model.get_relation(self.relation_name) is None:
            return (
                False,
                "Relation with s3-integrator charm missing, cannot create/restore backup.",
            )

        _, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            return False, f"Missing S3 parameters: {missing_parameters}"

        return True, ""

    @property
    def _can_initialise_stanza(self) -> bool:
        """Validates whether this unit can initialise a stanza."""
        # Don't allow stanza initialisation if this unit hasn't started the database
        # yet and either hasn't joined the peer relation yet or hasn't configured TLS
        # yet while other unit already has TLS enabled.
        return not (
            not self.charm._patroni.member_started and (len(self.charm._peers.data.keys()) == 2)
        )

    def _can_unit_perform_backup(self) -> tuple[bool, str | None]:
        """Validates whether this unit can perform a backup."""
        if self.charm.is_blocked:
            return False, "Unit is in a blocking state"

        # Check if this unit is the primary (if it was not possible to retrieve that information,
        # then show that the unit cannot perform a backup, because possibly the database is offline).
        try:
            is_primary = self.charm.is_primary
        except RetryError:
            return False, "Unit cannot perform backups as the database seems to be offline"

        # Only enable backups on primary if there are replicas but TLS is not enabled.
        if is_primary and self.charm.app.planned_units() > 1:
            return False, "Unit cannot perform backups as it is the cluster primary"

        if not self.charm._patroni.member_started:
            return False, "Unit cannot perform backups as it's not in running state"

        if "stanza" not in self.charm.app_peer_data:
            return False, "Stanza was not initialised"

        return self._are_backup_settings_ok()

    def can_use_s3_repository(self) -> tuple[bool, str]:
        """Returns whether the charm was configured to use another cluster repository."""
        # Check model uuid
        s3_parameters, _ = self._retrieve_s3_parameters()
        s3_model_uuid = self._read_content_from_s3(
            "model-uuid.txt",
            s3_parameters,
        )
        if s3_model_uuid and s3_model_uuid.strip() != self.model.uuid:
            logger.debug(
                f"can_use_s3_repository: incompatible model-uuid s3={s3_model_uuid.strip()}, local={self.model.uuid}"
            )
            return False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE

        try:
            return_code, stdout, stderr = self._execute_command(
                [
                    PGBACKREST_EXECUTABLE,
                    PGBACKREST_CONFIGURATION_FILE,
                    PGBACKREST_LOG_LEVEL_STDERR,
                    "info",
                    "--output=json",
                ],
                timeout=30,
            )
        except TimeoutExpired as e:
            # Raise an error if the connection timeouts, so the user has the possibility to
            # fix network issues and call juju resolve to re-trigger the hook that calls
            # this method.
            logger.error(f"error: {e!s} - please fix the error and call juju resolve on this unit")
            raise TimeoutError from e

        else:
            if return_code != 0:
                extracted_error = self._extract_error_message(stderr)
                logger.error(f"Failed to run pgbackrest: {extracted_error}")
                return False, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE

        for stanza in json.loads(stdout):
            if (stanza_name := stanza.get("name")) and stanza_name == "[invalid]":
                logger.error("Invalid stanza name from s3")
                return False, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE
            if stanza_name != self.stanza_name:
                logger.debug(
                    f"can_use_s3_repository: incompatible stanza name s3={stanza_name or ''}, local={self.stanza_name}"
                )
                return False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE

            return_code, system_identifier_from_instance, error = self._execute_command([
                f"/snap/charmed-postgresql/current/usr/lib/postgresql/{self.charm._patroni.get_postgresql_version().split('.')[0]}/bin/pg_controldata",
                POSTGRESQL_DATA_PATH,
            ])
            if return_code != 0:
                raise Exception(error)
            system_identifier_from_instance = next(
                line
                for line in system_identifier_from_instance.splitlines()
                if "Database system identifier" in line
            ).split(" ")[-1]
            stanza_dbs = stanza.get("db")
            system_identifier_from_stanza = (
                str(stanza_dbs[0]["system-id"]) if len(stanza_dbs) else None
            )
            if system_identifier_from_instance != system_identifier_from_stanza:
                logger.debug(
                    f"can_use_s3_repository: incompatible system identifier s3={system_identifier_from_stanza}, local={system_identifier_from_instance}"
                )
                return False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE

        return True, ""

    def _change_connectivity_to_database(self, connectivity: bool) -> None:
        """Enable or disable the connectivity to the database."""
        self.charm.unit_peer_data.update({"connectivity": "on" if connectivity else "off"})
        self.charm.update_config()

    def _construct_endpoint(self, s3_parameters: dict) -> str:
        """Construct the S3 service endpoint using the region.

        This is needed when the provided endpoint is from AWS, and it doesn't contain the region.
        """
        # Use the provided endpoint if a region is not needed.
        endpoint = s3_parameters["endpoint"]

        # Load endpoints data.
        loader = create_loader()
        data = loader.load_data("endpoints")

        # Construct the endpoint using the region.
        resolver = EndpointResolver(data)
        endpoint_data = resolver.construct_endpoint("s3", s3_parameters.get("region"))

        # Use the built endpoint if it is an AWS endpoint.
        if endpoint_data and endpoint.endswith(endpoint_data["dnsSuffix"]):
            endpoint = f"{endpoint.split('://')[0]}://{endpoint_data['hostname']}"

        return endpoint

    def _create_bucket_if_not_exists(self) -> None:
        s3_parameters, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            return

        bucket_name = s3_parameters["bucket"]
        region = s3_parameters.get("region", "")

        try:
            s3 = self._get_s3_session_resource(s3_parameters)
        except ValueError as e:
            logger.exception("Failed to create a session '%s' in region=%s.", bucket_name, region)
            raise e
        # Boto3 doesn't have typedefs
        bucket = s3.Bucket(bucket_name)  # type: ignore
        try:
            bucket.meta.client.head_bucket(Bucket=bucket_name)
            logger.info("Bucket %s exists.", bucket_name)
            exists = True
        except ConnectTimeoutError as e:
            # Re-raise the error if the connection timeouts, so the user has the possibility to
            # fix network issues and call juju resolve to re-trigger the hook that calls
            # this method.
            logger.error(f"error: {e!s} - please fix the error and call juju resolve on this unit")
            raise e
        except ClientError:
            logger.warning("Bucket %s doesn't exist or you don't have access to it.", bucket_name)
            exists = False
        except SSLError as e:
            logger.error(f"error: {e!s} - Is TLS enabled and CA chain set on S3?")
            raise e
        if exists:
            return

        try:
            bucket.create(CreateBucketConfiguration={"LocationConstraint": region})

            bucket.wait_until_exists()
            logger.info("Created bucket '%s' in region=%s", bucket_name, region)
        except ClientError as error:
            if error.response["Error"]["Code"] == "InvalidLocationConstraint":
                logger.info("Specified location-constraint is not valid, trying create without it")
                try:
                    bucket.create()

                    bucket.wait_until_exists()
                    logger.info("Created bucket '%s', ignored region=%s", bucket_name, region)
                except ClientError as error:
                    logger.exception(
                        "Couldn't create bucket named '%s' in region=%s.", bucket_name, region
                    )
                    raise error
            else:
                logger.exception(
                    "Couldn't create bucket named '%s' in region=%s.", bucket_name, region
                )
                raise error

    def _empty_data_files(self) -> bool:
        """Empty the PostgreSQL data directory in preparation of backup restore."""
        paths = [
            "/var/snap/charmed-postgresql/common/data/archive",
            POSTGRESQL_DATA_PATH,
            "/var/snap/charmed-postgresql/common/data/logs",
            "/var/snap/charmed-postgresql/common/data/temp",
        ]
        path = None
        try:
            for path in paths:
                path_object = Path(path)
                if path_object.exists() and path_object.is_dir():
                    for item in os.listdir(path):
                        item_path = os.path.join(path, item)
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.remove(item_path)
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
        except OSError as e:
            logger.warning(f"Failed to remove contents from {path} with error: {e!s}")
            return False

        return True

    def _execute_command(
        self,
        command: list[str],
        command_input: bytes | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        """Execute a command in the workload container."""
        # Input is generated by the charm
        process = run(  # noqa: S603
            command,
            input=command_input,
            capture_output=True,
            timeout=timeout,
        )
        return process.returncode, process.stdout.decode(), process.stderr.decode()

    @staticmethod
    def _extract_error_message(stderr: str) -> str:
        """Extract key error message from pgBackRest stderr output.

        Since we standardize all pgBackRest commands to use --log-level-stderr=warn,
        all errors and warnings are consistently written to stderr. This makes error
        extraction predictable and avoids potential log duplication issues.

        Args:
            stderr: Standard error from pgBackRest command containing errors/warnings.

        Returns:
            Extracted error message from stderr, prioritizing ERROR/WARN lines.
        """
        if not stderr.strip():
            return f"Unknown error occurred. Please check the logs at {PGBACKREST_LOGS_PATH}"

        # Extract lines with ERROR or WARN markers from pgBackRest stderr output
        error_lines = []
        for line in stderr.splitlines():
            if "ERROR:" in line or "WARN:" in line:
                # Clean up the line by removing debug prefixes like "P00  ERROR:"
                cleaned = re.sub(r"^.*?(ERROR:|WARN:)", r"\1", line).strip()
                error_lines.append(cleaned)

        # If we found error/warning lines, return them joined
        if error_lines:
            return "; ".join(error_lines)

        # Otherwise return the last non-empty line from stderr
        return stderr.strip().splitlines()[-1]

    def _format_backup_list(self, backup_list) -> str:
        """Formats provided list of backups as a table."""
        s3_parameters, _ = self._retrieve_s3_parameters()
        backups = [
            "Storage bucket name: {:s}".format(s3_parameters["bucket"]),
            "Backups base path: {:s}/backup/\n".format(s3_parameters["path"]),
            "{:<20s} | {:<19s} | {:<8s} | {:<20s} | {:<23s} | {:<20s} | {:<20s} | {:<8s} | {:s}".format(
                "backup-id",
                "action",
                "status",
                "reference-backup-id",
                "LSN start/stop",
                "start-time",
                "finish-time",
                "timeline",
                "backup-path",
            ),
        ]
        backups.append("-" * len(backups[2]))
        for (
            backup_id,
            backup_action,
            backup_status,
            reference,
            lsn_start_stop,
            start,
            stop,
            backup_timeline,
            path,
        ) in backup_list:
            backups.append(
                f"{backup_id:<20s} | {backup_action:<19s} | {backup_status:<8s} | {reference:<20s} | {lsn_start_stop:<23s} | {start:<20s} | {stop:<20s} | {backup_timeline:<8s} | {path:s}"
            )
        return "\n".join(backups)

    def _generate_backup_list_output(self) -> str:
        """Generates a list of backups in a formatted table.

        List contains successful and failed backups in order of ascending time.
        """
        backup_list = []
        return_code, output, stderr = self._execute_command([
            PGBACKREST_EXECUTABLE,
            PGBACKREST_CONFIGURATION_FILE,
            PGBACKREST_LOG_LEVEL_STDERR,
            "info",
            "--output=json",
        ])
        if return_code != 0:
            extracted_error = self._extract_error_message(stderr)
            raise ListBackupsError(f"Failed to list backups with error: {extracted_error}")

        backups = json.loads(output)[0]["backup"]
        for backup in backups:
            backup_id, backup_type = self._parse_backup_id(backup["label"])
            backup_action = f"{backup_type} backup"
            backup_reference = "None"
            if backup["reference"]:
                backup_reference, _ = self._parse_backup_id(backup["reference"][-1])
            lsn_start_stop = f"{backup['lsn']['start']} / {backup['lsn']['stop']}"
            time_start, time_stop = (
                datetime.strftime(datetime.fromtimestamp(stamp, UTC), "%Y-%m-%dT%H:%M:%SZ")
                for stamp in backup["timestamp"].values()
            )
            backup_timeline = (
                backup["archive"]["start"][:8].lstrip("0")
                if backup["archive"] and backup["archive"]["start"]
                else ""
            )
            backup_path = f"/{self.stanza_name}/{backup['label']}"
            error = backup["error"]
            backup_status = "finished"
            if error:
                backup_status = f"failed: {error}"
            backup_list.append((
                backup_id,
                backup_action,
                backup_status,
                backup_reference,
                lsn_start_stop,
                time_start,
                time_stop,
                backup_timeline,
                backup_path,
            ))

        for timeline, (_, timeline_id) in self._list_timelines().items():
            backup_list.append((
                timeline,
                "restore",
                "finished",
                "None",
                "n/a",
                timeline,
                "n/a",
                timeline_id,
                "n/a",
            ))

        backup_list.sort(key=lambda x: x[0])

        return self._format_backup_list(backup_list)

    def _list_backups(self, show_failed: bool, parse=True) -> dict[str, tuple[str, str]]:
        """Retrieve the list of backups.

        Args:
            show_failed: whether to also return the failed backups.
            parse: whether to convert backup labels to their IDs or not.

        Returns:
            a dict of previously created backups: id => (stanza, timeline) or an empty dict if there is no backups in
                the S3 bucket.
        """
        return_code, output, stderr = self._execute_command([
            PGBACKREST_EXECUTABLE,
            PGBACKREST_CONFIGURATION_FILE,
            PGBACKREST_LOG_LEVEL_STDERR,
            "info",
            "--output=json",
        ])
        if return_code != 0:
            extracted_error = self._extract_error_message(stderr)
            raise ListBackupsError(f"Failed to list backups with error: {extracted_error}")

        repository_info = next(iter(json.loads(output)), None)

        # If there are no backups, returns an empty dict.
        if repository_info is None:
            return dict[str, tuple[str, str]]()

        backups = repository_info["backup"]
        stanza_name = repository_info["name"]
        return dict[str, tuple[str, str]]({
            self._parse_backup_id(backup["label"])[0] if parse else backup["label"]: (
                stanza_name,
                backup["archive"]["start"][:8].lstrip("0")
                if backup["archive"] and backup["archive"]["start"]
                else "",
            )
            for backup in backups
            if show_failed or not backup["error"]
        })

    def _list_timelines(self) -> dict[str, tuple[str, str]]:
        """Lists the timelines from the pgBackRest stanza.

        Returns:
            a dict of timelines: id => (stanza, timeline) or an empty dict if there is no timelines in the S3 bucket.
        """
        return_code, output, stderr = self._execute_command([
            PGBACKREST_EXECUTABLE,
            PGBACKREST_CONFIGURATION_FILE,
            PGBACKREST_LOG_LEVEL_STDERR,
            "repo-ls",
            f"archive/{self.stanza_name}",
            "--filter=\\.history$",
            "--output=json",
        ])
        if return_code != 0:
            extracted_error = self._extract_error_message(stderr)
            raise ListBackupsError(f"Failed to list repository with error: {extracted_error}")

        repository = json.loads(output).items()
        if repository is None:
            return dict[str, tuple[str, str]]()

        return dict[str, tuple[str, str]]({
            datetime.strftime(
                datetime.fromtimestamp(timeline_object["time"], UTC),
                BACKUP_ID_FORMAT,
            ): (
                timeline.split("/")[1],
                timeline.split("/")[-1].split(".")[0].lstrip("0"),
            )
            for timeline, timeline_object in repository
            if timeline.endswith(".history") and not timeline.endswith("backup.history")
        })

    def _get_nearest_timeline(self, timestamp: str) -> tuple[str, str] | None:
        """Finds the nearest timeline or backup prior to the specified timeline.

        Returns:
            (stanza, timeline) of the nearest timeline or backup. None, if there are no matches.
        """
        timelines = self._list_backups(show_failed=False) | self._list_timelines()
        if timestamp == "latest":
            return max(timelines.items())[1] if len(timelines) > 0 else None
        filtered_timelines = [
            (timeline_key, timeline_object)
            for timeline_key, timeline_object in timelines.items()
            if datetime.strptime(timeline_key, BACKUP_ID_FORMAT)
            <= self._parse_psql_timestamp(timestamp)
        ]
        return max(filtered_timelines)[1] if len(filtered_timelines) > 0 else None

    def _is_psql_timestamp(self, timestamp: str) -> bool:
        if not re.match(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?([-+](?:\d{2}|\d{4}|\d{2}:\d{2}))?$",
            timestamp,
        ):
            return False
        try:
            self._parse_psql_timestamp(timestamp)
            return True
        except ValueError:
            return False

    def _parse_psql_timestamp(self, timestamp: str) -> datetime:
        """Intended to use with data only after _is_psql_timestamp check."""
        # With the python >= 3.11 only the datetime.fromisoformat will be sufficient without any regexes. Therefore,
        # it will not be required for the _is_psql_timestamp check that ensures intended regex execution.
        t = re.sub(r"([-+]\d{2})$", r"\1:00", timestamp)
        t = re.sub(r"([-+]\d{2})(\d{2})$", r"\1:\2", t)
        t = re.sub(r"\.(\d+)", lambda x: f".{x[1]:06}", t)
        dt = datetime.fromisoformat(t)
        # Convert to the timezone-naive
        if dt.tzinfo is not None and dt.tzinfo is not UTC:
            dt = dt.astimezone(tz=UTC)
        return dt.replace(tzinfo=None)

    def _parse_backup_id(self, label) -> tuple[str, str]:
        """Parse backup ID as a timestamp and its type."""
        if label[-1] == "F":
            timestamp = label
            backup_type = "full"
        elif label[-1] == "D":
            timestamp = label.split("_")[1]
            backup_type = "differential"
        elif label[-1] == "I":
            timestamp = label.split("_")[1]
            backup_type = "incremental"
        else:
            raise ValueError("Unknown label format for backup ID: %s", label)

        return (
            datetime.strftime(
                datetime.strptime(timestamp[:-1], PGBACKREST_BACKUP_ID_FORMAT),
                BACKUP_ID_FORMAT,
            ),
            backup_type,
        )

    def _initialise_stanza(self, event: HookEvent) -> bool:
        """Initialize the stanza.

        A stanza is the configuration for a PostgreSQL database cluster that defines where it is located, how it will
        be backed up, archiving options, etc. (more info in
        https://pgbackrest.org/user-guide.html#quickstart/configure-stanza).

        Returns:
            whether stanza initialization were successful.
        """
        # Enable stanza initialisation if the backup settings were fixed after being invalid
        # or pointing to a repository where there are backups from another cluster.
        if self.charm.is_blocked and self.charm.unit.status.message not in S3_BLOCK_MESSAGES:
            logger.warning("couldn't initialize stanza due to a blocked status, deferring event")
            event.defer()
            return False

        self.charm.set_unit_status(MaintenanceStatus("initialising stanza"))

        # Create the stanza.
        try:
            # If the tls is enabled, it requires all the units in the cluster to run the pgBackRest service to
            # successfully complete validation, and upon receiving the same parent event other units should start it.
            # Therefore, the first retry may fail due to the delay of these other units to start this service. 60s given
            # for that or else the s3 initialization sequence will fail.
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10), reraise=True):
                with attempt:
                    return_code, _, stderr = self._execute_command([
                        PGBACKREST_EXECUTABLE,
                        PGBACKREST_CONFIGURATION_FILE,
                        PGBACKREST_LOG_LEVEL_STDERR,
                        f"--stanza={self.stanza_name}",
                        "stanza-create",
                    ])
                    if return_code == 49:
                        # Raise an error if the connection timeouts, so the user has the possibility to
                        # fix network issues and call juju resolve to re-trigger the hook that calls
                        # this method.
                        logger.error(
                            f"error: {stderr} - please fix the error and call juju resolve on this unit"
                        )
                        raise TimeoutError
                    if return_code != 0:
                        raise Exception(stderr)
        except TimeoutError as e:
            raise e
        except Exception:
            # If the check command doesn't succeed, remove the stanza name
            # and rollback the configuration.
            logger.exception("Failed to initialise stanza:")
            self._s3_initialization_set_failure(FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE)
            return False

        self.start_stop_pgbackrest_service()

        # Rest of the successful s3 initialization sequence such as s3-initialization-start and s3-initialization-done
        # are left to the check_stanza func.
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "stanza": self.stanza_name,
            })
        else:
            self.charm.unit_peer_data.update({
                "stanza": self.stanza_name,
            })

        return True

    def check_stanza(self) -> bool:
        """Runs the pgbackrest stanza validation.

        Returns:
            whether stanza validation was successful.
        """
        # Update the configuration to use pgBackRest as the archiving mechanism.
        self.charm.update_config()

        self.charm.set_unit_status(MaintenanceStatus("checking stanza"))

        try:
            # If the tls is enabled, it requires all the units in the cluster to run the pgBackRest service to
            # successfully complete validation, and upon receiving the same parent event other units should start it.
            # Therefore, the first retry may fail due to the delay of these other units to start this service. 60s given
            # for that or else the s3 initialization sequence will fail.
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10), reraise=True):
                with attempt:
                    return_code, _, stderr = self._execute_command([
                        PGBACKREST_EXECUTABLE,
                        PGBACKREST_CONFIGURATION_FILE,
                        PGBACKREST_LOG_LEVEL_STDERR,
                        f"--stanza={self.stanza_name}",
                        "check",
                    ])
                    if return_code == PGBACKREST_ARCHIVE_TIMEOUT_ERROR_CODE:
                        # Raise an error if the archive command timeouts, so the user has the possibility
                        # to fix network issues and call juju resolve to re-trigger the hook that calls
                        # this method.
                        extracted_error = self._extract_error_message(stderr)
                        logger.error(
                            f"error: {extracted_error} - please fix the error and call juju resolve on this unit"
                        )
                        raise TimeoutError
                    if return_code != 0:
                        raise Exception(stderr)
            self.charm._set_primary_status_message()
        except TimeoutError as e:
            # Re-raise to put charm in error state (not blocked), allowing juju resolve
            raise e
        except Exception:
            # If the check command doesn't succeed, remove the stanza name
            # and rollback the configuration.
            logger.exception("Failed to check stanza:")
            self._s3_initialization_set_failure(FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE)
            self.charm.update_config()
            return False

        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "s3-initialization-start": "",
            })
        else:
            self.charm.unit_peer_data.update({"s3-initialization-done": "True"})

        return True

    def coordinate_stanza_fields(self) -> None:
        """Coordinate the stanza name between the primary and the leader units."""
        if (
            not self.charm.unit.is_leader()
            or "s3-initialization-start" not in self.charm.app_peer_data
        ):
            return

        for _unit, unit_data in self.charm._peers.data.items():
            if "s3-initialization-done" not in unit_data:
                continue

            self.charm.app_peer_data.update({
                "stanza": unit_data.get("stanza", ""),
                "s3-initialization-block-message": unit_data.get(
                    "s3-initialization-block-message", ""
                ),
                "s3-initialization-start": "",
                "s3-initialization-done": "True",
            })

            self.charm.update_config()
            break

    @property
    def _is_primary_pgbackrest_service_running(self) -> bool:
        if not self.charm.primary_endpoint:
            logger.warning("Failed to contact pgBackRest TLS server: no primary endpoint")
            return False
        return_code, _, stderr = self._execute_command([
            PGBACKREST_EXECUTABLE,
            PGBACKREST_LOG_LEVEL_STDERR,
            "server-ping",
            "--io-timeout=10",
            self.charm.primary_endpoint,
        ])
        if return_code != 0:
            extracted_error = self._extract_error_message(stderr)
            logger.warning(
                f"Failed to contact pgBackRest TLS server on {self.charm.primary_endpoint} with error {extracted_error}"
            )
        return return_code == 0

    def _credential_changed_checks(self, event: CredentialsChangedEvent) -> bool:
        if not self.charm.is_cluster_initialised or not self.charm.get_secret(
            UNIT_SCOPE, "internal-cert"
        ):
            logger.debug("Cannot set pgBackRest configurations, PostgreSQL has not yet started.")
            event.defer()
            return False

        # Prevents config change in bad state, so DB peer relations change event will not cause patroni related errors.
        if self.charm.unit.status.message == CANNOT_RESTORE_PITR:
            logger.info("Cannot change S3 configuration in bad PITR restore status")
            event.defer()
            return False

        # Prevents S3 change in the middle of restoring backup and patroni / pgbackrest errors caused by that.
        if self.charm.is_cluster_restoring_backup or self.charm.is_cluster_restoring_to_time:
            logger.info("Cannot change S3 configuration during restore")
            event.defer()
            return False

        if not self._render_pgbackrest_conf_file():
            logger.debug("Cannot set pgBackRest configurations, missing configurations.")
            return False

        if not self._can_initialise_stanza:
            logger.debug("Cannot initialise stanza yet.")
            event.defer()
            return False
        return True

    def _on_s3_credential_changed(self, event: CredentialsChangedEvent) -> None:
        """Call the stanza initialization when the credentials or the connection info change."""
        if not self._credential_changed_checks(event):
            return

        # Start the pgBackRest service for the check_stanza to be successful. It's required to run on all the units if the tls is enabled.
        try:
            self.start_stop_pgbackrest_service()
        except RetryError:
            logger.debug("Cannot initialise service yet.")
            event.defer()
            return

        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "s3-initialization-block-message": "",
                "s3-initialization-start": time.asctime(time.gmtime()),
                "stanza": "",
                "s3-initialization-done": "",
            })
            if not self.charm.is_primary:
                self.charm._set_primary_status_message()

        if self.charm.is_primary and "s3-initialization-done" not in self.charm.unit_peer_data:
            self._on_s3_credential_changed_primary(event)

        if self.charm.is_standby_leader:
            logger.info(
                "S3 credentials will not be connected on standby cluster until it becomes primary"
            )

    def _on_s3_credential_changed_primary(self, event: HookEvent) -> bool:
        """Stanza must be cleared before calling this function."""
        self.charm.update_config()

        try:
            self._create_bucket_if_not_exists()
        except (ClientError, ValueError, ParamValidationError, SSLError):
            self._s3_initialization_set_failure(FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE)
            return False

        can_use_s3_repository, validation_message = self.can_use_s3_repository()
        if not can_use_s3_repository:
            self._s3_initialization_set_failure(validation_message)
            return False

        if not self._initialise_stanza(event):
            return False

        if not self.check_stanza():
            return False

        s3_parameters, _ = self._retrieve_s3_parameters()
        self._upload_content_to_s3(
            self.model.uuid,
            "model-uuid.txt",
            s3_parameters,
        )

        return True

    def _on_s3_credential_gone(self, _) -> None:
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "stanza": "",
                "s3-initialization-start": "",
                "s3-initialization-done": "",
                "s3-initialization-block-message": "",
            })
        self.charm.unit_peer_data.update({
            "stanza": "",
            "s3-initialization-start": "",
            "s3-initialization-done": "",
            "s3-initialization-block-message": "",
        })
        if self.charm.is_blocked and self.charm.unit.status.message in S3_BLOCK_MESSAGES:
            self.charm._set_primary_status_message()

    def _on_create_backup_action(self, event) -> None:
        """Request that pgBackRest creates a backup."""
        backup_type = event.params.get("type", "full")
        if backup_type not in BACKUP_TYPE_OVERRIDES:
            error_message = f"Invalid backup type: {backup_type}. Possible values: {', '.join(BACKUP_TYPE_OVERRIDES.keys())}."
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
            return

        if (
            backup_type in ["differential", "incremental"]
            and len(self._list_backups(show_failed=False)) == 0
        ):
            error_message = (
                f"Invalid backup type: {backup_type}. No previous full backup to reference."
            )
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
            return

        logger.info(f"A {backup_type} backup has been requested on unit")
        can_unit_perform_backup, validation_message = self._can_unit_perform_backup()
        if not can_unit_perform_backup:
            logger.error(f"Backup failed: {validation_message}")
            event.fail(validation_message)
            return

        # Retrieve the S3 Parameters to use when uploading the backup logs to S3.
        s3_parameters, _ = self._retrieve_s3_parameters()

        # Test uploading metadata to S3 to test credentials before backup.
        datetime_backup_requested = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        metadata = f"""Date Backup Requested: {datetime_backup_requested}
Model Name: {self.model.name}
Application Name: {self.model.app.name}
Unit Name: {self.charm.unit.name}
Juju Version: {self.charm.model.juju_version!s}
"""
        if not self._upload_content_to_s3(
            metadata,
            f"backup/{self.stanza_name}/latest",
            s3_parameters,
        ):
            error_message = "Failed to upload metadata to provided S3"
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
            return

        if not self.charm.is_primary:
            # Create a rule to mark the cluster as in a creating backup state and update
            # the Patroni configuration.
            self._change_connectivity_to_database(connectivity=False)

        self.charm.set_unit_status(MaintenanceStatus("creating backup"))
        # Set flag due to missing in progress backups on JSON output
        # (reference: https://github.com/pgbackrest/pgbackrest/issues/2007)
        self.charm.update_config(is_creating_backup=True)

        self._run_backup(event, s3_parameters, datetime_backup_requested, backup_type)

        if not self.charm.is_primary:
            # Remove the rule that marks the cluster as in a creating backup state
            # and update the Patroni configuration.
            self._change_connectivity_to_database(connectivity=True)

        self.charm.update_config(is_creating_backup=False)
        self.charm.set_unit_status(ActiveStatus())

    def _run_backup(
        self,
        event: ActionEvent,
        s3_parameters: dict,
        datetime_backup_requested: str,
        backup_type: str,
    ) -> None:
        command = [
            PGBACKREST_EXECUTABLE,
            PGBACKREST_CONFIGURATION_FILE,
            PGBACKREST_LOG_LEVEL_STDERR,
            f"--stanza={self.stanza_name}",
            "--log-level-console=debug",
            f"--type={BACKUP_TYPE_OVERRIDES[backup_type]}",
            "backup",
        ]
        if self.charm.is_primary:
            # Force the backup to run in the primary if it's not possible to run it
            # on the replicas (that happens when TLS is not enabled).
            command.append("--no-backup-standby")
        return_code, stdout, stderr = self._execute_command(command)
        if return_code != 0:
            logger.error(stderr)

            # Recover the backup id from the logs.
            backup_label_stdout_line = re.findall(
                r"(new backup label = )([0-9]{8}[-][0-9]{6}[F])$", stdout, re.MULTILINE
            )
            if len(backup_label_stdout_line) > 0:
                backup_id = backup_label_stdout_line[0][1]
            else:
                # Generate a backup id from the current date and time if the backup failed before
                # generating the backup label (our backup id).
                backup_id = self._generate_fake_backup_id(backup_type)

            # Upload the logs to S3.
            logs = f"""Stdout:
{stdout}

Stderr:
{stderr}
"""
            self._upload_content_to_s3(
                logs,
                f"backup/{self.stanza_name}/{backup_id}/backup.log",
                s3_parameters,
            )
            extracted_error = self._extract_error_message(stderr)
            error_message = f"Failed to backup PostgreSQL with error: {extracted_error}"
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
        else:
            try:
                backup_id = list(self._list_backups(show_failed=True).keys())[-1]
            except ListBackupsError:
                error_message = "Failed to retrieve backup id"
                logger.exception(error_message)
                logger.error(f"Backup failed: {error_message}")
                event.fail(error_message)
                return

            # Upload the logs to S3 and fail the action if it doesn't succeed.
            logs = f"""Stdout:
{stdout}

Stderr:
{stderr}
"""
            if not self._upload_content_to_s3(
                logs,
                f"backup/{self.stanza_name}/{backup_id}/backup.log",
                s3_parameters,
            ):
                error_message = "Error uploading logs to S3"
                logger.error(f"Backup failed: {error_message}")
                event.fail(error_message)
            else:
                logger.info(f"Backup succeeded: with backup-id {datetime_backup_requested}")
                event.set_results({"backup-status": "backup created"})

    def _on_list_backups_action(self, event) -> None:
        """List the previously created backups."""
        are_backup_settings_ok, validation_message = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            logger.warning(validation_message)
            event.fail(validation_message)
            return

        try:
            formatted_list = self._generate_backup_list_output()
            event.set_results({"backups": formatted_list})
        except ListBackupsError as e:
            logger.exception(e)
            event.fail(f"Failed to list PostgreSQL backups with error: {e!s}")

    def _on_restore_action(self, event):  # noqa: C901
        """Request that pgBackRest restores a backup."""
        if not self._pre_restore_checks(event):
            return

        backup_id = event.params.get("backup-id")
        restore_to_time = event.params.get("restore-to-time")
        logger.info(
            f"A restore with backup-id {backup_id}"
            f"{f' to time point {restore_to_time}' if restore_to_time else ''}"
            f" has been requested on the unit"
        )

        # Validate the provided backup id and restore to time.
        logger.info("Validating provided backup-id and restore-to-time")
        try:
            backups = self._list_backups(show_failed=False)
            timelines = self._list_timelines()
            is_backup_id_real = backup_id and backup_id in backups
            is_backup_id_timeline = backup_id and not is_backup_id_real and backup_id in timelines
            if backup_id and not is_backup_id_real and not is_backup_id_timeline:
                error_message = f"Invalid backup-id: {backup_id}"
                logger.error(f"Restore failed: {error_message}")
                event.fail(error_message)
                return
            if is_backup_id_timeline and not restore_to_time:
                error_message = "Cannot restore to the timeline without restore-to-time parameter"
                logger.error(f"Restore failed: {error_message}")
                event.fail(error_message)
                return
            if is_backup_id_real:
                restore_stanza_timeline = backups[backup_id]
            elif is_backup_id_timeline:
                restore_stanza_timeline = timelines[backup_id]
            else:
                backups_list = list(backups.values())
                if (
                    restore_to_time == "latest"
                    and timelines is not None
                    and max(timelines.values() or [backups_list[0]]) not in backups_list
                ):
                    error_message = "There is no base backup created from the latest timeline"
                    logger.error(f"Restore failed: {error_message}")
                    event.fail(error_message)
                    return
                restore_stanza_timeline = self._get_nearest_timeline(restore_to_time)
                if not restore_stanza_timeline:
                    error_message = f"Can't find the nearest timeline before timestamp {restore_to_time} to restore"
                    logger.error(f"Restore failed: {error_message}")
                    event.fail(error_message)
                    return
                logger.info(
                    f"Chosen timeline {restore_stanza_timeline[1]} as nearest for the specified timestamp {restore_to_time}"
                )
        except ListBackupsError as e:
            logger.exception(e)
            error_message = "Failed to retrieve backups list"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return

        self.charm.set_unit_status(MaintenanceStatus("restoring backup"))

        # Stop the database service before performing the restore.
        logger.info("Stopping database service")
        if not self.charm._patroni.stop_patroni():
            error_message = "Failed to stop database service"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return

        # Temporarily disabling patroni service auto-restart. This is required as point-in-time-recovery can fail
        # on restore, therefore during cluster bootstrapping process. In this case, we need be able to check patroni
        # service status and logs. Disabling auto-restart feature is essential to prevent wrong status indicated
        # and logs reading race condition (as logs cleared / moved with service restarts).
        if not self.charm.override_patroni_restart_condition("no", "restore-backup"):
            error_message = "Failed to override Patroni restart condition"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            self._restart_database()
            return

        logger.info("Removing the contents of the data directory")
        if not self._empty_data_files():
            error_message = "Failed to remove contents of the data directory"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            self._restart_database()
            return

        # Mark the cluster as in a restoring backup state and update the Patroni configuration.
        logger.info("Configuring Patroni to restore the backup")
        self.charm.app_peer_data.update({
            "restoring-backup": self._fetch_backup_from_id(backup_id) if is_backup_id_real else "",
            "restore-stanza": restore_stanza_timeline[0],
            "restore-timeline": restore_stanza_timeline[1] if restore_to_time else "",
            "restore-to-time": restore_to_time or "",
            "s3-initialization-block-message": "",
        })
        self.charm.update_config()

        # Start the database to start the restore process.
        logger.info("Configuring Patroni to restore the backup")
        self.charm._patroni.start_patroni()

        # Remove previous cluster information to make it possible to initialise a new cluster.
        logger.info("Removing previous cluster information")
        return_code, _, stderr = self._execute_command(
            [
                "charmed-postgresql.patronictl",
                "-c",
                f"{PATRONI_CONF_PATH}/patroni.yaml",
                "remove",
                self.charm.cluster_name,
            ],
            command_input=f"{self.charm.cluster_name}\nYes I am aware".encode(),
            timeout=10,
        )
        if return_code != 0:
            extracted_error = self._extract_error_message(stderr)
            error_message = (
                f"Failed to remove previous cluster information with error: {extracted_error}"
            )
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return

        event.set_results({"restore-status": "restore started"})

    def _generate_fake_backup_id(self, backup_type: str) -> str:
        """Creates a backup id for failed backup operations (to store log file)."""
        if backup_type == "full":
            return datetime.strftime(datetime.now(), "%Y%m%d-%H%M%SF")
        if backup_type == "differential":
            backups = list(self._list_backups(show_failed=False, parse=False).keys())
            last_full_backup = None
            for label in backups[::-1]:
                if label.endswith("F"):
                    last_full_backup = label
                    break

            if last_full_backup is None:
                raise TypeError("Differential backup requested but no previous full backup")
            return f"{last_full_backup}_{datetime.strftime(datetime.now(), '%Y%m%d-%H%M%SD')}"
        if backup_type == "incremental":
            backups = list(self._list_backups(show_failed=False, parse=False).keys())
            if not backups:
                raise TypeError("Incremental backup requested but no previous successful backup")
            return f"{backups[-1]}_{datetime.strftime(datetime.now(), '%Y%m%d-%H%M%SI')}"
        else:
            raise Exception("Invalid backup type")

    def _fetch_backup_from_id(self, backup_id: str) -> str | None:
        """Fetches backup's pgbackrest label from backup id."""
        timestamp = f"{datetime.strftime(datetime.strptime(backup_id, '%Y-%m-%dT%H:%M:%SZ'), '%Y%m%d-%H%M%S')}"
        backups = self._list_backups(show_failed=False, parse=False).keys()
        for label in backups:
            if timestamp in label:
                return label

        return None

    def _pre_restore_checks(self, event: ActionEvent) -> bool:
        """Run some checks before starting the restore.

        Returns:
            a boolean indicating whether restore should be run.
        """
        are_backup_settings_ok, validation_message = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            logger.error(f"Restore failed: {validation_message}")
            event.fail(validation_message)
            return False

        if not event.params.get("backup-id") and event.params.get("restore-to-time") is None:
            error_message = "Either backup-id or restore-to-time parameters need to be provided to be able to do restore"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        # Quick check for timestamp format
        restore_to_time = event.params.get("restore-to-time")
        if (
            restore_to_time
            and restore_to_time != "latest"
            and not self._is_psql_timestamp(restore_to_time)
        ):
            error_message = "Bad restore-to-time format"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking if cluster is in blocked state")
        if self.charm.is_blocked and self.charm.unit.status.message not in [
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
            CANNOT_RESTORE_PITR,
        ]:
            error_message = "Cluster or unit is in a blocking state"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking that the cluster does not have more than one unit")
        if self.charm.app.planned_units() > 1:
            error_message = (
                "Unit cannot restore backup as there are more than one unit in the cluster"
            )
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking that cluster does not have an active async replication relation")
        for relation in [
            self.model.get_relation(REPLICATION_CONSUMER_RELATION),
            self.model.get_relation(REPLICATION_OFFER_RELATION),
        ]:
            if not relation:
                continue
            error_message = "Unit cannot restore backup with an active async replication relation"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking that cluster does not have an active logical replication relation")
        # if self.model.get_relation(LOGICAL_REPLICATION_RELATION) or len(
        #     self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ())
        # ):
        #     error_message = (
        #         "Unit cannot restore backup with an active logical replication connection"
        #     )
        #     logger.error(f"Restore failed: {error_message}")
        #     event.fail(error_message)
        #     return False

        logger.info("Checking that this unit was already elected the leader unit")
        if not self.charm.unit.is_leader():
            error_message = "Unit cannot restore backup as it was not elected the leader unit yet"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        return True

    def _render_pgbackrest_conf_file(self) -> bool:
        # Open the template pgbackrest.conf file.
        s3_parameters, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            logger.warning(
                f"Cannot set pgBackRest configurations due to missing S3 parameters: {missing_parameters}"
            )
            return False

        if self._tls_ca_chain_filename != "":
            self.charm._patroni.render_file(
                self._tls_ca_chain_filename, "\n".join(s3_parameters["tls-ca-chain"]), 0o644
            )

        with open("templates/pgbackrest.conf.j2") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            enable_tls=len(self.charm._peer_members_ips) > 0,
            peer_endpoints=self.charm._peer_members_ips,
            path=s3_parameters["path"],
            data_path=f"{POSTGRESQL_DATA_PATH}",
            log_path=f"{PGBACKREST_LOGS_PATH}",
            region=s3_parameters.get("region"),
            endpoint=s3_parameters["endpoint"],
            bucket=s3_parameters["bucket"],
            s3_uri_style=s3_parameters["s3-uri-style"],
            tls_ca_chain=self._tls_ca_chain_filename,
            access_key=s3_parameters["access-key"],
            secret_key=s3_parameters["secret-key"],
            stanza=self.stanza_name,
            storage_path=self.charm._storage_path,
            user=BACKUP_USER,
            retention_full=s3_parameters["delete-older-than-days"],
            process_max=max(self.charm.cpu_count - 2, 1),
        )
        # Render pgBackRest config file.
        self.charm._patroni.render_file(f"{PGBACKREST_CONF_PATH}/pgbackrest.conf", rendered, 0o640)

        # Render the logrotate configuration file.
        with open("templates/pgbackrest.logrotate.j2") as file:
            template = Template(file.read())
            self.charm._patroni.render_file(
                PGBACKREST_LOGROTATE_FILE, template.render(), 0o644, change_owner=False
            )

        return True

    def _restart_database(self) -> None:
        """Removes the restoring backup flag and restart the database."""
        self.charm.app_peer_data.update({"restoring-backup": "", "restore-to-time": ""})
        self.charm.update_config()
        self.charm._patroni.start_patroni()

    def _retrieve_s3_parameters(self) -> tuple[dict, list[str]]:
        """Retrieve S3 parameters from the S3 integrator relation."""
        s3_parameters = self.s3_client.get_s3_connection_info()
        required_parameters = [
            "bucket",
            "access-key",
            "secret-key",
        ]
        missing_required_parameters = [
            param for param in required_parameters if param not in s3_parameters
        ]
        if missing_required_parameters:
            logger.warning(
                f"Missing required S3 parameters in relation with S3 integrator: {missing_required_parameters}"
            )
            return {}, missing_required_parameters

        # Add some sensible defaults (as expected by the code) for missing optional parameters
        s3_parameters.setdefault("endpoint", "https://s3.amazonaws.com")
        s3_parameters.setdefault("path", "")
        s3_parameters.setdefault("s3-uri-style", "host")
        s3_parameters.setdefault("delete-older-than-days", "9999999")

        # Strip whitespaces from all parameters.
        for key, value in s3_parameters.items():
            if isinstance(value, str):
                s3_parameters[key] = value.strip()

        # Clean up extra slash symbols to avoid issues on 3rd-party storages
        # like Ceph Object Gateway (radosgw).
        s3_parameters["endpoint"] = s3_parameters["endpoint"].rstrip("/")
        s3_parameters["path"] = (
            f"/{s3_parameters['path'].strip('/')}"  # The slash in the beginning is required by pgBackRest.
        )
        s3_parameters["bucket"] = s3_parameters["bucket"].strip("/")

        return s3_parameters, []

    def start_stop_pgbackrest_service(self) -> bool:
        """Start or stop the pgBackRest TLS server service.

        Returns:
            a boolean indicating whether the operation succeeded.
        """
        # Ignore this operation if backups settings aren't ok.
        are_backup_settings_ok, _ = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            return True

        # Update pgBackRest configuration (to update the TLS settings).
        if not self._render_pgbackrest_conf_file():
            return False

        snap_cache = snap.SnapCache()
        charmed_postgresql_snap = snap_cache["charmed-postgresql"]
        if not charmed_postgresql_snap.present:
            logger.error("Cannot start/stop service, snap is not yet installed.")
            return False

        # Stop the service if TLS is not enabled or there are no replicas.
        if len(self.charm._peer_members_ips) == 0 or self.charm._patroni.get_standby_leader():
            charmed_postgresql_snap.stop(services=["pgbackrest-service"])
            return True

        # Don't start the service if the service hasn't started yet in the primary.
        if not self.charm.is_primary and not self._is_primary_pgbackrest_service_running:
            return False

        # Start the service.
        charmed_postgresql_snap.restart(services=["pgbackrest-service"])
        return True

    def _upload_content_to_s3(
        self,
        content: str,
        s3_path: str,
        s3_parameters: dict,
    ) -> bool:
        """Uploads the provided contents to the provided S3 bucket relative to the path from the S3 config.

        Args:
            content: The content to upload to S3
            s3_path: The path to which to upload the content
            s3_parameters: A dictionary containing the S3 parameters
                The following are expected keys in the dictionary: bucket, region,
                endpoint, access-key and secret-key

        Returns:
            a boolean indicating success.
        """
        bucket_name = s3_parameters["bucket"]
        processed_s3_path = os.path.join(s3_parameters["path"], s3_path).lstrip("/")
        try:
            logger.info(f"Uploading content to bucket={bucket_name}, path={processed_s3_path}")
            s3 = self._get_s3_session_resource(s3_parameters)
            # Boto3 doesn't have typedefs
            bucket = s3.Bucket(bucket_name)  # type: ignore

            with tempfile.NamedTemporaryFile() as temp_file:
                temp_file.write(content.encode("utf-8"))
                temp_file.flush()
                bucket.upload_file(temp_file.name, processed_s3_path)
        except Exception as e:
            logger.exception(
                f"Failed to upload content to S3 bucket={bucket_name}, path={processed_s3_path}",
                exc_info=e,
            )
            return False

        return True

    def _read_content_from_s3(self, s3_path: str, s3_parameters: dict) -> str | None:
        """Reads specified content from the provided S3 bucket relative to the path from the S3 config.

        Args:
            s3_path: The S3 path from which download the content
            s3_parameters: A dictionary containing the S3 parameters
                The following are expected keys in the dictionary: bucket, region,
                endpoint, access-key and secret-key

        Returns:
            a string with the content if object is successfully downloaded and None if file is not existing or error
            occurred during download.
        """
        bucket_name = s3_parameters["bucket"]
        processed_s3_path = os.path.join(s3_parameters["path"], s3_path).lstrip("/")
        try:
            logger.info(f"Reading content from bucket={bucket_name}, path={processed_s3_path}")
            s3 = self._get_s3_session_resource(s3_parameters)
            # Boto3 doesn't have typedefs
            bucket = s3.Bucket(bucket_name)  # type: ignore
            with BytesIO() as buf:
                bucket.download_fileobj(processed_s3_path, buf)
                return buf.getvalue().decode("utf-8")
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                logger.info(
                    f"No such object to read from S3 bucket={bucket_name}, path={processed_s3_path}"
                )
            else:
                logger.exception(
                    f"Failed to read content from S3 bucket={bucket_name}, path={processed_s3_path}",
                    exc_info=e,
                )
        except Exception as e:
            logger.exception(
                f"Failed to read content from S3 bucket={bucket_name}, path={processed_s3_path}",
                exc_info=e,
            )

        return None

    def _s3_initialization_set_failure(
        self, block_message: str, update_leader_status: bool = True
    ):
        """Sets failed s3 initialization status with corresponding block_message in the app_peer_data (leader) or unit_peer_data (primary).

        Args:
            block_message: s3 initialization block message
            update_leader_status: whether to update leader status (with s3-initialization-block-message set already)
                immediately; defaults to True
        """
        if self.charm.unit.is_leader():
            # If performed on the leader, then leader == primary and there is no need to sync s3 data between two
            # different units. Therefore, there is no need for s3-initialization-done key, and it is sufficient to clear
            # s3-initialization-start key only.
            self.charm.app_peer_data.update({
                "s3-initialization-block-message": block_message,
                "s3-initialization-start": "",
                "stanza": "",
            })
            if update_leader_status:
                self.charm._set_primary_status_message()
        else:
            self.charm.unit_peer_data.update({
                "s3-initialization-block-message": block_message,
                "s3-initialization-done": "True",
                "stanza": "",
            })
