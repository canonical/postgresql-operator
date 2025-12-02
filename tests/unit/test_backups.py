# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from os import cpu_count
from pathlib import PosixPath
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import ANY, MagicMock, PropertyMock, call, mock_open, patch

import botocore as botocore
import pytest
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
from jinja2 import Template
from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, Unit
from ops.testing import Harness
from tenacity import RetryError, wait_fixed

from backups import ListBackupsError
from charm import PostgresqlOperatorCharm
from constants import PEER

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
S3_PARAMETERS_RELATION = "s3-parameters"


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    peer_rel_id = harness.add_relation(PEER, "postgresql")
    harness.add_relation_unit(peer_rel_id, "postgresql/0")
    harness.begin()
    yield harness
    harness.cleanup()


def test_stanza_name(harness):
    assert (
        harness.charm.backup.stanza_name
        == f"{harness.charm.model.name}.{harness.charm.cluster_name}"
    )


def test_tls_ca_chain_filename(harness):
    # Test when the TLS CA chain is not available.
    assert harness.charm.backup._tls_ca_chain_filename == ""

    # Test when the TLS CA chain is available.
    with harness.hooks_disabled():
        remote_application = "s3-integrator"
        s3_rel_id = harness.add_relation(S3_PARAMETERS_RELATION, remote_application)
        harness.update_relation_data(
            s3_rel_id,
            remote_application,
            {
                "bucket": "fake-bucket",
                "access-key": "fake-access-key",
                "secret-key": "fake-secret-key",
                "tls-ca-chain": '["fake-tls-ca-chain"]',
            },
        )
    assert (
        harness.charm.backup._tls_ca_chain_filename
        == "/var/snap/charmed-postgresql/common/pgbackrest-tls-ca-chain.crt"
    )


def test_are_backup_settings_ok(harness):
    # Test without S3 relation.
    assert harness.charm.backup._are_backup_settings_ok() == (
        False,
        "Relation with s3-integrator charm missing, cannot create/restore backup.",
    )

    # Test when there are missing S3 parameters.
    harness.add_relation(S3_PARAMETERS_RELATION, "s3-integrator")
    assert harness.charm.backup._are_backup_settings_ok() == (
        False,
        "Missing S3 parameters: ['bucket', 'access-key', 'secret-key']",
    )

    # Test when all required parameters are provided.
    with patch("charm.PostgreSQLBackups._retrieve_s3_parameters") as _retrieve_s3_parameters:
        _retrieve_s3_parameters.return_value = ["bucket", "access-key", "secret-key"], []
        assert harness.charm.backup._are_backup_settings_ok() == (True, None)


def test_can_initialise_stanza(harness):
    with patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started:
        # Test when Patroni or PostgreSQL hasn't started yet
        # and the unit hasn't joined the peer relation yet.
        _member_started.return_value = False
        assert not harness.charm.backup._can_initialise_stanza

        # Test when the unit hasn't configured TLS yet while other unit already has TLS enabled.
        harness.add_relation_unit(
            harness.model.get_relation(PEER).id, f"{harness.charm.app.name}/1"
        )
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER).id,
                f"{harness.charm.app.name}/1",
                {"tls": "enabled"},
            )
        assert not harness.charm.backup._can_initialise_stanza

        # Test when everything is ok to initialise the stanza.
        _member_started.return_value = True
        assert harness.charm.backup._can_initialise_stanza


def test_can_unit_perform_backup(harness):
    with (
        patch("charm.PostgreSQLBackups._are_backup_settings_ok") as _are_backup_settings_ok,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("ops.model.Application.planned_units") as _planned_units,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        # Test when the charm fails to retrieve the primary.
        _is_primary.side_effect = RetryError(last_attempt=1)
        assert harness.charm.backup._can_unit_perform_backup() == (
            False,
            "Unit cannot perform backups as the database seems to be offline",
        )

        # Test when the unit is in a blocked state.
        _is_primary.side_effect = None
        _is_primary.return_value = True
        harness.charm.unit.status = BlockedStatus("fake blocked state")
        assert harness.charm.backup._can_unit_perform_backup() == (
            False,
            "Unit is in a blocking state",
        )

        # Test when running the check in the primary, there are replicas and TLS is enabled.
        harness.charm.unit.status = ActiveStatus()
        _planned_units.return_value = 2
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"tls": "True"},
            )
        assert harness.charm.backup._can_unit_perform_backup() == (
            False,
            "Unit cannot perform backups as it is the cluster primary",
        )

        # Test when running the check in a replica and TLS is disabled.
        _is_primary.return_value = False
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"tls": ""},
            )
        assert harness.charm.backup._can_unit_perform_backup() == (
            False,
            "Unit cannot perform backups as TLS is not enabled",
        )

        # Test when Patroni or PostgreSQL hasn't started yet.
        _is_primary.return_value = True
        _member_started.return_value = False
        assert harness.charm.backup._can_unit_perform_backup() == (
            False,
            "Unit cannot perform backups as it's not in running state",
        )

        # Test when the stanza was not initialised yet.
        _member_started.return_value = True
        assert harness.charm.backup._can_unit_perform_backup() == (
            False,
            "Stanza was not initialised",
        )

        # Test when S3 parameters are not ok.
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                {"stanza": harness.charm.backup.stanza_name},
            )
        _are_backup_settings_ok.return_value = (False, "fake error message")
        assert harness.charm.backup._can_unit_perform_backup() == (False, "fake error message")

        # Test when everything is ok to run a backup.
        _are_backup_settings_ok.return_value = (True, None)
        assert harness.charm.backup._can_unit_perform_backup() == (True, None)


def test_can_use_s3_repository(harness):
    with (
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch(
            "charm.Patroni.get_postgresql_version", return_value="14.10"
        ) as _get_postgresql_version,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch(
            "charm.PostgreSQLBackups._retrieve_s3_parameters",
            return_value=({"path": "example"}, None),
        ),
        patch("charm.PostgreSQLBackups._read_content_from_s3") as _read_content_from_s3,
    ):
        # Test with bad model-uuid.
        _read_content_from_s3.return_value = "bad"
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        )

        # Test when nothing is returned from the pgBackRest info command.
        _read_content_from_s3.return_value = harness.model.uuid
        _execute_command.side_effect = TimeoutExpired(cmd="fake command", timeout=30)
        with pytest.raises(TimeoutError):
            harness.charm.backup.can_use_s3_repository()
            assert False

        # Test with bad pgBackRest error code.
        _execute_command.side_effect = None
        _execute_command.return_value = (1, "", "")
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
        )

        pgbackrest_info_same_cluster_backup_output = (
            0,
            f'[{{"db": [{{"system-id": "12345"}}], "name": "{harness.charm.backup.stanza_name}"}}]',
            "",
        )

        # Invalid stanza name
        pgbackrest_info_other_cluster_name_backup_output = (
            0,
            '[{"db": [{"system-id": "12345"}], "name": "[invalid]"}]',
            "",
        )
        same_instance_system_identifier_output = (
            0,
            "Database system identifier:           12345",
            "",
        )
        _execute_command.side_effect = [
            pgbackrest_info_other_cluster_name_backup_output,
            same_instance_system_identifier_output,
        ]
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
        )

        # Test when the cluster system id can be retrieved, but it's different from the stanza system id.
        pgbackrest_info_other_cluster_system_id_backup_output = (
            0,
            f'[{{"db": [{{"system-id": "12345"}}], "name": "{harness.charm.backup.stanza_name}"}}]',
            "",
        )
        other_instance_system_identifier_output = (
            0,
            "Database system identifier:           67890",
            "",
        )
        _execute_command.side_effect = [
            pgbackrest_info_other_cluster_system_id_backup_output,
            other_instance_system_identifier_output,
        ]
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        )

        # Test when the cluster system id can be retrieved, but it's different from the stanza system id.
        pgbackrest_info_other_cluster_name_backup_output = (
            0,
            f'[{{"db": [{{"system-id": "12345"}}], "name": "another-model.{harness.charm.cluster_name}"}}]',
            "",
        )
        same_instance_system_identifier_output = (
            0,
            "Database system identifier:           12345",
            "",
        )
        _execute_command.side_effect = [
            pgbackrest_info_other_cluster_name_backup_output,
            same_instance_system_identifier_output,
        ]
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        )

        # Test when the workload is not running.
        _member_started.return_value = False
        _execute_command.side_effect = [
            pgbackrest_info_same_cluster_backup_output,
            other_instance_system_identifier_output,
        ]
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        )

        # Test when there is no backup from another cluster in the S3 repository.
        _execute_command.side_effect = [
            pgbackrest_info_same_cluster_backup_output,
            same_instance_system_identifier_output,
        ]
        assert harness.charm.backup.can_use_s3_repository() == (True, None)

        # Empty db
        _execute_command.side_effect = None
        _execute_command.return_value = (1, "", "")
        pgbackrest_info_other_cluster_name_backup_output = (
            0,
            f'[{{"db": [], "name": "another-model.{harness.charm.cluster_name}"}}]',
            "",
        )
        assert harness.charm.backup.can_use_s3_repository() == (
            False,
            FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
        )


def test_construct_endpoint(harness):
    # Test with an AWS endpoint without region.
    s3_parameters = {"endpoint": "https://s3.amazonaws.com", "region": ""}
    assert harness.charm.backup._construct_endpoint(s3_parameters) == "https://s3.amazonaws.com"

    # Test with an AWS endpoint with region.
    s3_parameters["region"] = "us-east-1"
    assert (
        harness.charm.backup._construct_endpoint(s3_parameters)
        == "https://s3.us-east-1.amazonaws.com"
    )

    # Test with another cloud endpoint.
    s3_parameters["endpoint"] = "https://storage.googleapis.com"
    assert (
        harness.charm.backup._construct_endpoint(s3_parameters) == "https://storage.googleapis.com"
    )


@pytest.mark.parametrize(
    "tls_ca_chain_filename",
    ["", "/var/snap/charmed-postgresql/common/pgbackrest-tls-ca-chain.crt"],
)
def test_create_bucket_if_not_exists(harness, tls_ca_chain_filename):
    with (
        patch("boto3.session.Session.resource") as _resource,
        patch(
            "charm.PostgreSQLBackups._tls_ca_chain_filename",
            new_callable=PropertyMock(return_value=tls_ca_chain_filename),
        ) as _tls_ca_chain_filename,
        patch("charm.PostgreSQLBackups._retrieve_s3_parameters") as _retrieve_s3_parameters,
        patch("backups.Config") as _config,
    ):
        # Test when there are missing S3 parameters.
        _retrieve_s3_parameters.return_value = ([], ["bucket", "access-key", "secret-key"])
        harness.charm.backup._create_bucket_if_not_exists()
        _resource.assert_not_called()

        # Test when the charm fails to create a boto3 session.
        _retrieve_s3_parameters.return_value = (
            {
                "bucket": "test-bucket",
                "access-key": "test-access-key",
                "secret-key": "test-secret-key",
                "endpoint": "test-endpoint",
                "region": "test-region",
            },
            [],
        )
        _resource.side_effect = ValueError
        with pytest.raises(ValueError):
            harness.charm.backup._create_bucket_if_not_exists()
            assert False

        # Test when the bucket already exists.
        _resource.reset_mock()
        _config.reset_mock()
        _resource.side_effect = None
        head_bucket = _resource.return_value.Bucket.return_value.meta.client.head_bucket
        create = _resource.return_value.Bucket.return_value.create
        wait_until_exists = _resource.return_value.Bucket.return_value.wait_until_exists
        harness.charm.backup._create_bucket_if_not_exists()
        _resource.assert_called_once_with(
            "s3",
            endpoint_url="test-endpoint",
            verify=(tls_ca_chain_filename or None),
            config=_config.return_value,
        )
        _config.assert_called_once_with(
            # https://github.com/boto/boto3/issues/4400#issuecomment-2600742103
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        head_bucket.assert_called_once()
        create.assert_not_called()
        wait_until_exists.assert_not_called()

        # Test when the bucket doesn't exist.
        head_bucket.reset_mock()
        head_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": "SomeFakeException", "message": "fake error"}},
            operation_name="fake operation name",
        )
        harness.charm.backup._create_bucket_if_not_exists()
        head_bucket.assert_called_once()
        create.assert_called_once()
        wait_until_exists.assert_called_once()

        # Test when the bucket creation fails.
        head_bucket.reset_mock()
        create.reset_mock()
        wait_until_exists.reset_mock()
        create.side_effect = ClientError(
            error_response={"Error": {"Code": "SomeFakeException", "message": "fake error"}},
            operation_name="fake operation name",
        )
        with pytest.raises(ClientError):
            harness.charm.backup._create_bucket_if_not_exists()
            assert False
        head_bucket.assert_called_once()
        create.assert_called_once()
        wait_until_exists.assert_not_called()

        # Test when the bucket creation fails with InvalidLocationConstraint.
        head_bucket.reset_mock()
        create.reset_mock()
        wait_until_exists.reset_mock()
        create.side_effect = ClientError(
            error_response={
                "Error": {"Code": "InvalidLocationConstraint", "message": "fake error"}
            },
            operation_name="fake operation name",
        )
        with pytest.raises(ClientError):
            harness.charm.backup._create_bucket_if_not_exists()
            assert False
        head_bucket.assert_called_once()
        want = [
            call(CreateBucketConfiguration={"LocationConstraint": "test-region"}),
            call(),
        ]
        create.assert_has_calls(want)
        wait_until_exists.assert_not_called()

        # Test when the bucket creation fails due to a timeout error.
        head_bucket.reset_mock()
        create.reset_mock()
        wait_until_exists.reset_mock()
        head_bucket.side_effect = botocore.exceptions.ConnectTimeoutError(
            endpoint_url="fake endpoint URL"
        )
        with pytest.raises(botocore.exceptions.ConnectTimeoutError):
            harness.charm.backup._create_bucket_if_not_exists()
            assert False
        head_bucket.assert_called_once()
        create.assert_not_called()
        wait_until_exists.assert_not_called()


def test_empty_data_files(harness):
    with (
        patch("shutil.rmtree") as _rmtree,
        patch("pathlib.Path.is_dir") as _is_dir,
        patch("pathlib.Path.exists") as _exists,
    ):
        # Test when the data directory doesn't exist.
        _exists.return_value = False
        assert harness.charm.backup._empty_data_files()
        _rmtree.assert_not_called()

        # Test when the removal of the data files fails.
        path = PosixPath("/var/snap/charmed-postgresql/common/var/lib/postgresql")
        _exists.return_value = True
        _is_dir.return_value = True
        _rmtree.side_effect = OSError
        assert not harness.charm.backup._empty_data_files()
        _rmtree.assert_called_once_with(path)

        # Test when data files are successfully removed.
        _rmtree.reset_mock()
        _rmtree.side_effect = None
        assert harness.charm.backup._empty_data_files()
        _rmtree.assert_called_once_with(path)


def test_change_connectivity_to_database(harness):
    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        peer_rel_id = harness.model.get_relation(PEER).id
        # Ensure that there is no connectivity info in the unit relation databag.
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"connectivity": ""},
            )

        # Test when connectivity should be turned on.
        harness.charm.backup._change_connectivity_to_database(True)
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {"connectivity": "on"}
        _update_config.assert_called_once()

        # Test when connectivity should be turned off.
        _update_config.reset_mock()
        harness.charm.backup._change_connectivity_to_database(False)
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {
            "connectivity": "off"
        }
        _update_config.assert_called_once()


def test_execute_command(harness):
    with (
        patch("backups.run") as _run,
        patch("pwd.getpwnam") as _getpwnam,
    ):
        # Test when the command fails.
        command = ["rm", "-r", "/var/lib/postgresql/data/pgdata"]
        _run.return_value = CompletedProcess(command, 1, b"", b"fake stderr")
        assert harness.charm.backup._execute_command(command) == (1, "", "fake stderr")
        _run.assert_called_once_with(
            command, input=None, capture_output=True, preexec_fn=ANY, timeout=None
        )
        _getpwnam.assert_called_once_with("snap_daemon")

        # Test when the command runs successfully.
        _run.reset_mock()
        _getpwnam.reset_mock()
        _run.side_effect = None
        _run.return_value = CompletedProcess(command, 0, b"fake stdout", b"")
        assert harness.charm.backup._execute_command(
            command, command_input=b"fake input", timeout=5
        ) == (0, "fake stdout", "")
        _run.assert_called_once_with(
            command, input=b"fake input", capture_output=True, preexec_fn=ANY, timeout=5
        )
        _getpwnam.assert_called_once_with("snap_daemon")


def test_format_backup_list(harness):
    with patch(
        "charms.data_platform_libs.v0.s3.S3Requirer.get_s3_connection_info"
    ) as _get_s3_connection_info:
        # Test when there are no backups.
        _get_s3_connection_info.return_value = {
            "bucket": " /test-bucket/ ",
            "access-key": " test-access-key ",
            "secret-key": " test-secret-key ",
            "path": " test-path/ ",
        }
        assert (
            harness.charm.backup._format_backup_list([])
            == """Storage bucket name: test-bucket
Backups base path: /test-path/backup/

backup-id            | action              | status   | reference-backup-id  | LSN start/stop          | start-time           | finish-time          | timeline | backup-path
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------"""
        )

        # Test when there are backups.
        backup_list = [
            (
                "2023-01-01T09:00:00Z",
                "full backup",
                "failed: fake error",
                "None",
                "0/3000000 / 0/5000000",
                "2023-01-01T09:00:00Z",
                "2023-01-01T09:00:05Z",
                "1",
                "a/b/c",
            ),
            (
                "2023-01-01T10:00:00Z",
                "full backup",
                "finished",
                "None",
                "0/5000000 / 0/7000000",
                "2023-01-01T10:00:00Z",
                "2023-01-01T10:00:07Z",
                "A",
                "a/b/d",
            ),
            (
                "2023-01-01T11:00:00Z",
                "restore",
                "finished",
                "None",
                "n/a",
                "2023-01-01T11:00:00Z",
                "n/a",
                "B",
                "n/a",
            ),
        ]
        assert (
            harness.charm.backup._format_backup_list(backup_list)
            == """Storage bucket name: test-bucket
Backups base path: /test-path/backup/

backup-id            | action              | status   | reference-backup-id  | LSN start/stop          | start-time           | finish-time          | timeline | backup-path
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
2023-01-01T09:00:00Z | full backup         | failed: fake error | None                 | 0/3000000 / 0/5000000   | 2023-01-01T09:00:00Z | 2023-01-01T09:00:05Z | 1        | a/b/c
2023-01-01T10:00:00Z | full backup         | finished | None                 | 0/5000000 / 0/7000000   | 2023-01-01T10:00:00Z | 2023-01-01T10:00:07Z | A        | a/b/d
2023-01-01T11:00:00Z | restore             | finished | None                 | n/a                     | 2023-01-01T11:00:00Z | n/a                  | B        | n/a"""
        )


def test_generate_backup_list_output(harness):
    with (
        patch(
            "charms.data_platform_libs.v0.s3.S3Requirer.get_s3_connection_info"
        ) as _get_s3_connection_info,
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
    ):
        _get_s3_connection_info.return_value = {
            "bucket": " /test-bucket/ ",
            "access-key": " test-access-key ",
            "secret-key": " test-secret-key ",
            "path": " test-path/ ",
        }
        # Test when no backups are returned.
        _execute_command.side_effect = [(0, '[{"backup":[]}]', ""), (0, "{}", "")]
        assert (
            harness.charm.backup._generate_backup_list_output()
            == """Storage bucket name: test-bucket
Backups base path: /test-path/backup/

backup-id            | action              | status   | reference-backup-id  | LSN start/stop          | start-time           | finish-time          | timeline | backup-path
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------"""
        )

        # Test when backups are returned.
        _execute_command.side_effect = [
            (
                0,
                '[{"backup":[{"archive":{"start":"00000001000000000000000B"},"label":"20230101-090000F","error":"fake error","reference":null,"lsn":{"start":"0/3000000","stop":"0/5000000"},"timestamp":{"start":1719866711,"stop":1719866714}}]}]',
                "",
            ),
            (
                0,
                '{".":{"type":"path"},"archive/None.postgresql/14-1/00000002.history":{"type": "file","size": 32,"time": 1728937652}}',
                "",
            ),
        ]
        assert (
            harness.charm.backup._generate_backup_list_output()
            == """Storage bucket name: test-bucket
Backups base path: /test-path/backup/

backup-id            | action              | status   | reference-backup-id  | LSN start/stop          | start-time           | finish-time          | timeline | backup-path
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
2023-01-01T09:00:00Z | full backup         | failed: fake error | None                 | 0/3000000 / 0/5000000   | 2024-07-01T20:45:11Z | 2024-07-01T20:45:14Z | 1        | /None.postgresql/20230101-090000F
2024-10-14T20:27:32Z | restore             | finished | None                 | n/a                     | 2024-10-14T20:27:32Z | n/a                  | 2        | n/a"""
        )


def test_list_backups(harness):
    with patch("charm.PostgreSQLBackups._execute_command") as _execute_command:
        # Test when the command that list the backups fails.
        _execute_command.return_value = (1, "", "fake stderr")
        with pytest.raises(ListBackupsError):
            harness.charm.backup._list_backups(show_failed=True)
            assert False

        # Test when no backups are available.
        _execute_command.return_value = (0, "[]", "")
        assert harness.charm.backup._list_backups(show_failed=True) == dict[str, tuple[str, str]]()

        # Test when some backups are available.
        _execute_command.return_value = (
            0,
            '[{"backup":[{"archive":{"start":"00000001000000000000000B"},"label":"20230101-090000F","error":"fake error"},{"archive":{"start":"0000000A000000000000000B"},"label":"20230101-100000F","error":null}],"name":"test-stanza"}]',
            "",
        )
        assert harness.charm.backup._list_backups(show_failed=True) == dict[str, tuple[str, str]]([
            ("2023-01-01T09:00:00Z", ("test-stanza", "1")),
            ("2023-01-01T10:00:00Z", ("test-stanza", "A")),
        ])

        # Test when some backups are available, but it's not desired to list failed backups.
        assert harness.charm.backup._list_backups(show_failed=False) == dict[str, tuple[str, str]]([
            ("2023-01-01T10:00:00Z", ("test-stanza", "A"))
        ])


def test_initialise_stanza(harness):
    with (
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("backups.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
        patch(
            "charm.PostgreSQLBackups._s3_initialization_set_failure"
        ) as _s3_initialization_set_failure,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id

        mock_event = MagicMock()

        # Test when it's in a blocked state other than the ones can be solved by new S3 settings.
        harness.charm.unit.status = BlockedStatus("fake blocked state")
        harness.charm.backup._initialise_stanza(mock_event)
        _execute_command.assert_not_called()
        mock_event.defer.assert_called_once()

        # Test when the blocked state is any of the blocked stated that can be solved
        # by new S3 settings, but the stanza creation fails.
        mock_event.reset_mock()
        stanza_creation_command = [
            "charmed-postgresql.pgbackrest",
            "--config=/var/snap/charmed-postgresql/current/etc/pgbackrest/pgbackrest.conf",
            f"--stanza={harness.charm.backup.stanza_name}",
            "stanza-create",
        ]
        _execute_command.return_value = (1, "", "fake stderr")
        for blocked_state in [
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
            FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE,
            FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
        ]:
            _s3_initialization_set_failure.reset_mock()
            _execute_command.reset_mock()
            harness.charm.unit.status = BlockedStatus(blocked_state)
            harness.charm.backup._initialise_stanza(mock_event)
            _execute_command.assert_called_with(stanza_creation_command)
            mock_event.defer.assert_not_called()
            # Only the leader will display the blocked status.
            assert isinstance(harness.charm.unit.status, MaintenanceStatus)
            _s3_initialization_set_failure.assert_called_once_with(
                FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE
            )

        # Test when the failure in the stanza creation is due to a timeout.
        _execute_command.reset_mock()
        _execute_command.return_value = (49, "", "fake stderr")
        with pytest.raises(TimeoutError):
            harness.charm.backup._initialise_stanza(mock_event)
            assert False
        mock_event.defer.assert_not_called()

        # Test when the archiving is working correctly (pgBackRest check command succeeds)
        # and the unit is not the leader.
        _execute_command.reset_mock()
        _execute_command.return_value = (0, "fake stdout", "")
        _member_started.return_value = True
        harness.charm.backup._initialise_stanza(mock_event)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {
            "stanza": f"{harness.charm.model.name}.postgresql",
        }
        assert isinstance(harness.charm.unit.status, MaintenanceStatus)
        mock_event.defer.assert_not_called()

        # Test when the unit is the leader.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(peer_rel_id, harness.charm.unit.name, {"stanza": ""})
        harness.charm.backup._initialise_stanza(mock_event)
        _update_config.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "stanza": f"{harness.charm.model.name}.postgresql",
        }
        _member_started.assert_not_called()
        _reload_patroni_configuration.assert_not_called()
        mock_event.defer.assert_not_called()
        assert isinstance(harness.charm.unit.status, MaintenanceStatus)


def test_check_stanza(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("backups.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
        patch(
            "charm.PostgreSQLBackups._s3_initialization_set_failure"
        ) as _s3_initialization_set_failure,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        # Set peer data flag
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                {"s3-initialization-start": "test-stanza"},
            )

        _execute_command.return_value = (49, "", "fake stderr")
        assert not harness.charm.backup.check_stanza()
        _reload_patroni_configuration.assert_not_called()
        _set_primary_status_message.assert_not_called()
        _s3_initialization_set_failure.assert_called_once_with(
            FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE
        )

        # Test when the failure in the stanza check is due to an archive timeout.
        _execute_command.reset_mock()
        _s3_initialization_set_failure.reset_mock()
        _execute_command.return_value = (82, "", "fake stderr")
        with pytest.raises(TimeoutError):
            harness.charm.backup.check_stanza()
            assert False
        _s3_initialization_set_failure.assert_not_called()

        _execute_command.reset_mock()
        _s3_initialization_set_failure.reset_mock()
        _execute_command.return_value = (0, "fake stdout", "")
        assert harness.charm.backup.check_stanza()
        _execute_command.assert_called_once()
        _set_primary_status_message.assert_called_once()
        _s3_initialization_set_failure.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {
            "s3-initialization-done": "True"
        }

        with harness.hooks_disabled():
            harness.set_leader()
        assert harness.charm.backup.check_stanza()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}


def test_coordinate_stanza_fields(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        stanza_name = f"{harness.charm.model.name}.{harness.charm.app.name}"

        peer_data_primary_error = {
            "s3-initialization-done": "True",
            "s3-initialization-block-message": ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        }
        peer_data_primary_ok = {
            "s3-initialization-done": "True",
            "stanza": stanza_name,
        }
        peer_data_leader_start = {
            "s3-initialization-start": "Thu Feb 24 05:00:00 2022",
        }
        peer_data_leader_error = {
            "s3-initialization-done": "True",
            "s3-initialization-block-message": ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        }
        peer_data_leader_ok = {"s3-initialization-done": "True", "stanza": stanza_name}
        peer_data_clean = {
            "s3-initialization-start": "",
            "s3-initialization-done": "",
            "s3-initialization-block-message": "",
            "stanza": "",
        }

        # Add a new unit to the relation.
        new_unit_name = "postgresql-k8s/1"
        new_unit = Unit(new_unit_name, None, harness.charm.app._backend, {})
        harness.add_relation_unit(peer_rel_id, new_unit_name)

        # Test with clear values.
        harness.charm.backup.coordinate_stanza_fields()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}
        assert harness.get_relation_data(peer_rel_id, new_unit) == {}

        # Test with primary failed prior leader s3 initialization sequence started.
        with harness.hooks_disabled():
            harness.update_relation_data(peer_rel_id, new_unit_name, peer_data_primary_error)
        harness.charm.backup.coordinate_stanza_fields()
        _update_config.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}
        assert harness.get_relation_data(peer_rel_id, new_unit) == peer_data_primary_error

        # Test with non-leader unit.
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id, harness.charm.app.name, peer_data_leader_start
            )
        harness.charm.backup.coordinate_stanza_fields()
        _update_config.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == peer_data_leader_start
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}
        assert harness.get_relation_data(peer_rel_id, new_unit) == peer_data_primary_error

        # Leader should sync fail result from the primary.
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.backup.coordinate_stanza_fields()
        _update_config.assert_called_once()
        _reload_patroni_configuration.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == peer_data_leader_error
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}
        assert harness.get_relation_data(peer_rel_id, new_unit) == peer_data_primary_error

        # Test with successful result from the primary.
        _update_config.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(peer_rel_id, harness.charm.app.name, peer_data_clean)
            harness.update_relation_data(
                peer_rel_id, harness.charm.app.name, peer_data_leader_start
            )
            harness.update_relation_data(peer_rel_id, new_unit_name, peer_data_clean)
            harness.update_relation_data(peer_rel_id, new_unit_name, peer_data_primary_ok)
        harness.charm.backup.coordinate_stanza_fields()
        _update_config.assert_called_once()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == peer_data_leader_ok
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}
        assert harness.get_relation_data(peer_rel_id, new_unit) == peer_data_primary_ok

        # Test when leader is waiting for the primary result.
        _update_config.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(peer_rel_id, harness.charm.app.name, peer_data_clean)
            harness.update_relation_data(
                peer_rel_id, harness.charm.app.name, peer_data_leader_start
            )
            harness.update_relation_data(peer_rel_id, new_unit_name, peer_data_clean)
        harness.charm.backup.coordinate_stanza_fields()
        _update_config.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == peer_data_leader_start
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}
        assert harness.get_relation_data(peer_rel_id, new_unit) == {}


def test_is_primary_pgbackrest_service_running(harness):
    with (
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
        patch("charm.Patroni.get_primary") as _get_primary,
    ):
        # Test when the pgBackRest fails to contact the primary server.
        _get_primary.side_effect = None
        _execute_command.return_value = (1, "", "fake stderr")
        assert not harness.charm.backup._is_primary_pgbackrest_service_running
        _execute_command.assert_called_once()

        # Test when the endpoint is not generated.
        _execute_command.reset_mock()
        _primary_endpoint.return_value = None
        assert not harness.charm.backup._is_primary_pgbackrest_service_running
        _execute_command.assert_not_called()

        # Test when the pgBackRest succeeds on contacting the primary server.
        _execute_command.reset_mock()
        _execute_command.return_value = (0, "fake stdout", "")
        _primary_endpoint.return_value = "fake_endpoint"
        assert harness.charm.backup._is_primary_pgbackrest_service_running
        _execute_command.assert_called_once()


def test_on_s3_credential_changed(harness):
    with (
        patch(
            "charm.PostgreSQLBackups._render_pgbackrest_conf_file"
        ) as _render_pgbackrest_conf_file,
        patch(
            "charm.PostgreSQLBackups._can_initialise_stanza", new_callable=PropertyMock
        ) as _can_initialise_stanza,
        patch(
            "charm.PostgreSQLBackups.start_stop_pgbackrest_service"
        ) as _start_stop_pgbackrest_service,
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
        patch(
            "charm.PostgreSQLBackups._on_s3_credential_changed_primary"
        ) as _on_s3_credential_changed_primary,
        patch("ops.framework.EventBase.defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_leader", new_callable=PropertyMock
        ) as _is_standby_leader,
        patch("time.gmtime"),
        patch("time.asctime", return_value="Thu Feb 24 05:00:00 2022"),
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        # Test when the cluster was not initialised yet.
        s3_rel_id = harness.add_relation(S3_PARAMETERS_RELATION, "s3-integrator")
        harness.charm.backup.s3_client.on.credentials_changed.emit(
            relation=harness.model.get_relation(S3_PARAMETERS_RELATION, s3_rel_id)
        )
        _defer.assert_called_once()
        _render_pgbackrest_conf_file.assert_not_called()

        # Test when the cluster is already initialised, but the charm fails to render
        # the pgBackRest configuration file due to missing S3 parameters.
        _defer.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True"},
            )
        _render_pgbackrest_conf_file.return_value = False
        harness.charm.backup.s3_client.on.credentials_changed.emit(
            relation=harness.model.get_relation(S3_PARAMETERS_RELATION, s3_rel_id)
        )
        _defer.assert_not_called()
        _render_pgbackrest_conf_file.assert_called_once()
        _can_initialise_stanza.assert_not_called()

        # Test when it's not possible to initialise the stanza in this unit.
        _render_pgbackrest_conf_file.return_value = True
        _can_initialise_stanza.return_value = False
        harness.charm.backup.s3_client.on.credentials_changed.emit(
            relation=harness.model.get_relation(S3_PARAMETERS_RELATION, s3_rel_id)
        )
        _can_initialise_stanza.assert_called_once()
        _defer.assert_called_once()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when unit is not a leader and can't do any peer data changes
        _is_primary.return_value = False
        _can_initialise_stanza.return_value = True
        harness.charm.backup.s3_client.on.credentials_changed.emit(
            relation=harness.model.get_relation(S3_PARAMETERS_RELATION, s3_rel_id)
        )
        _is_standby_leader.assert_called_once()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "cluster_initialised": "True"
        }

        # Test when unit is a leader but not primary
        _is_standby_leader.reset_mock()
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.backup.s3_client.on.credentials_changed.emit(
            relation=harness.model.get_relation(S3_PARAMETERS_RELATION, s3_rel_id)
        )
        _set_primary_status_message.assert_called_once()
        _on_s3_credential_changed_primary.assert_not_called()
        _is_standby_leader.assert_called_once()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "cluster_initialised": "True",
            "s3-initialization-start": "Thu Feb 24 05:00:00 2022",
        }

        # Test when unit is a leader and primary
        _is_primary.return_value = True
        _is_standby_leader.reset_mock()
        _set_primary_status_message.reset_mock()
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.backup.s3_client.on.credentials_changed.emit(
            relation=harness.model.get_relation(S3_PARAMETERS_RELATION, s3_rel_id)
        )
        _on_s3_credential_changed_primary.assert_called_once()
        _set_primary_status_message.assert_not_called()
        _is_standby_leader.assert_called_once()


def test_on_s3_credential_changed_primary(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch(
            "charm.PostgreSQLBackups._create_bucket_if_not_exists"
        ) as _create_bucket_if_not_exists,
        patch(
            "charm.PostgreSQLBackups._s3_initialization_set_failure"
        ) as _s3_initialization_set_failure,
        patch("charm.PostgreSQLBackups.can_use_s3_repository") as _can_use_s3_repository,
        patch("charm.PostgreSQLBackups._initialise_stanza") as _initialise_stanza,
        patch("charm.PostgreSQLBackups.check_stanza") as _check_stanza,
        patch(
            "charm.PostgreSQLBackups._retrieve_s3_parameters",
            return_value=({"path": "example"}, None),
        ),
        patch("charm.PostgreSQLBackups._upload_content_to_s3") as _upload_content_to_s3,
    ):
        mock_event = MagicMock()

        _create_bucket_if_not_exists.side_effect = ValueError()
        assert not harness.charm.backup._on_s3_credential_changed_primary(mock_event)
        _reload_patroni_configuration.assert_not_called()
        _create_bucket_if_not_exists.assert_called_once()
        _s3_initialization_set_failure.assert_called_once_with(
            FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE
        )
        _can_use_s3_repository.assert_not_called()

        _s3_initialization_set_failure.reset_mock()
        _create_bucket_if_not_exists.side_effect = None
        _can_use_s3_repository.return_value = (False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE)
        assert not harness.charm.backup._on_s3_credential_changed_primary(mock_event)
        _can_use_s3_repository.assert_called_once()
        _s3_initialization_set_failure.assert_called_once_with(
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE
        )
        _initialise_stanza.assert_not_called()

        _can_use_s3_repository.return_value = (True, None)
        _initialise_stanza.return_value = False
        assert not harness.charm.backup._on_s3_credential_changed_primary(mock_event)
        _initialise_stanza.assert_called_once()
        _check_stanza.assert_not_called()

        _initialise_stanza.return_value = True
        _check_stanza.return_value = False
        assert not harness.charm.backup._on_s3_credential_changed_primary(mock_event)
        _check_stanza.assert_called_once()
        _upload_content_to_s3.assert_not_called()

        _check_stanza.return_value = True
        assert harness.charm.backup._on_s3_credential_changed_primary(mock_event)
        _upload_content_to_s3.assert_called_once()


def test_on_s3_credential_gone(harness):
    with patch(
        "charm.PostgresqlOperatorCharm._set_primary_status_message"
    ) as _set_primary_status_message:
        full_peer_s3_parameters = {
            "stanza": "test-stanza",
            "s3-initialization-start": "Thu Feb 24 05:00:00 2022",
            "s3-initialization-done": "True",
            "s3-initialization-block-message": ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        }

        peer_rel_id = harness.model.get_relation(PEER).id
        # Test that unrelated blocks will remain
        harness.charm.unit.status = BlockedStatus("test block")
        harness.charm.backup._on_s3_credential_gone(None)
        _set_primary_status_message.assert_not_called()

        # Test that s3 related blocks will be cleared
        harness.charm.unit.status = BlockedStatus(ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE)
        harness.charm.backup._on_s3_credential_gone(None)
        _set_primary_status_message.assert_called_once()

        # Test removal of relation data when the unit is not the leader.
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                full_peer_s3_parameters,
            )
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                full_peer_s3_parameters,
            )
        harness.charm.backup._on_s3_credential_gone(None)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == full_peer_s3_parameters
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}

        # Test removal of relation data when the unit is the leader.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                full_peer_s3_parameters,
            )
        harness.charm.backup._on_s3_credential_gone(None)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit) == {}


def test_on_create_backup_action(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch(
            "charm.PostgreSQLBackups._change_connectivity_to_database"
        ) as _change_connectivity_to_database,
        patch("charm.PostgreSQLBackups._list_backups") as _list_backups,
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
        patch("charm.PostgreSQLBackups._upload_content_to_s3") as _upload_content_to_s3,
        patch("backups.datetime") as _datetime,
        patch("ops.JujuVersion.from_environ") as _from_environ,
        patch("charm.PostgreSQLBackups._retrieve_s3_parameters") as _retrieve_s3_parameters,
        patch("charm.PostgreSQLBackups._can_unit_perform_backup") as _can_unit_perform_backup,
    ):
        # Test when the unit cannot perform a backup because of type.
        mock_event = MagicMock()
        mock_event.params = {"type": "wrong"}
        harness.charm.backup._on_create_backup_action(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the unit cannot perform a backup because of preflight check.
        mock_event = MagicMock()
        mock_event.params = {"type": "full"}
        _can_unit_perform_backup.return_value = (False, "fake validation message")
        harness.charm.backup._on_create_backup_action(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to upload a file to S3.
        mock_event.reset_mock()
        _can_unit_perform_backup.return_value = (True, None)
        mock_s3_parameters = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "endpoint": "test-endpoint",
            "path": "test-path",
            "region": "test-region",
        }
        _retrieve_s3_parameters.return_value = (
            mock_s3_parameters,
            [],
        )
        _datetime.now.return_value.strftime.return_value = "2023-01-01T09:00:00Z"
        _from_environ.return_value = "test-juju-version"
        _upload_content_to_s3.return_value = False
        expected_metadata = f"""Date Backup Requested: 2023-01-01T09:00:00Z
Model Name: {harness.charm.model.name}
Application Name: {harness.charm.model.app.name}
Unit Name: {harness.charm.unit.name}
Juju Version: test-juju-version
"""
        harness.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_called_once_with(
            expected_metadata,
            f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/latest",
            mock_s3_parameters,
        )
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup is of type diff/incr when there's no previous full backup.
        mock_event.reset_mock()
        mock_event.params = {"type": "differential"}
        _upload_content_to_s3.return_value = True
        _is_primary.return_value = True
        harness.charm.backup._on_create_backup_action(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup fails.
        mock_event.reset_mock()
        mock_event.params = {"type": "full"}
        _upload_content_to_s3.return_value = True
        _is_primary.return_value = True
        _execute_command.return_value = (1, "", "fake error")
        harness.charm.backup._on_create_backup_action(mock_event)
        update_config_calls = [
            call(is_creating_backup=True),
            call(is_creating_backup=False),
        ]
        _update_config.assert_has_calls(update_config_calls)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup succeeds but the charm fails to upload the backup logs.
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _upload_content_to_s3.side_effect = [True, False]
        _execute_command.side_effect = None
        _execute_command.return_value = (0, "fake stdout", "fake stderr")
        _list_backups.return_value = {"2023-01-01T09:00:00Z": harness.charm.backup.stanza_name}
        _update_config.reset_mock()
        harness.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls([
            call(
                expected_metadata,
                f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/latest",
                mock_s3_parameters,
            ),
            call(
                "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                mock_s3_parameters,
            ),
        ])
        _update_config.assert_has_calls(update_config_calls)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup succeeds (including the upload of the backup logs).
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _upload_content_to_s3.side_effect = None
        _upload_content_to_s3.return_value = True
        _update_config.reset_mock()
        harness.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls([
            call(
                expected_metadata,
                f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/latest",
                mock_s3_parameters,
            ),
            call(
                "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                mock_s3_parameters,
            ),
        ])
        _change_connectivity_to_database.assert_not_called()
        _update_config.assert_has_calls(update_config_calls)
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once()

        # Test when this unit is a replica (the connectivity to the database should be changed).
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _is_primary.return_value = False
        harness.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls([
            call(
                expected_metadata,
                f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/latest",
                mock_s3_parameters,
            ),
            call(
                "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                f"backup/{harness.charm.model.name}.{harness.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                mock_s3_parameters,
            ),
        ])
        assert _change_connectivity_to_database.call_count == 2
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"backup-status": "backup created"})


def test_on_list_backups_action(harness):
    with (
        patch(
            "charm.PostgreSQLBackups._generate_backup_list_output"
        ) as _generate_backup_list_output,
        patch("charm.PostgreSQLBackups._are_backup_settings_ok") as _are_backup_settings_ok,
    ):
        # Test when not all backup settings are ok.
        mock_event = MagicMock()
        _are_backup_settings_ok.return_value = (False, "fake validation message")
        harness.charm.backup._on_list_backups_action(mock_event)
        mock_event.fail.assert_called_once()
        _generate_backup_list_output.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to generate the backup list output.
        mock_event.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        _generate_backup_list_output.side_effect = ListBackupsError
        harness.charm.backup._on_list_backups_action(mock_event)
        _generate_backup_list_output.assert_called_once()
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the charm succeeds on generating the backup list output.
        mock_event.reset_mock()
        _generate_backup_list_output.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        _generate_backup_list_output.side_effect = None
        _generate_backup_list_output.return_value = """backup-id             | backup-type  | backup-status
    ----------------------------------------------------
    2023-01-01T09:00:00Z  | full         | failed: fake error
    2023-01-01T10:00:00Z  | full         | finished"""
        harness.charm.backup._on_list_backups_action(mock_event)
        _generate_backup_list_output.assert_called_once()
        mock_event.set_results.assert_called_once_with({
            "backups": """backup-id             | backup-type  | backup-status
    ----------------------------------------------------
    2023-01-01T09:00:00Z  | full         | failed: fake error
    2023-01-01T10:00:00Z  | full         | finished"""
        })
        mock_event.fail.assert_not_called()


def test_list_timelines(harness):
    with patch("charm.PostgreSQLBackups._execute_command") as _execute_command:
        _execute_command.return_value = (0, "{}", "")
        assert harness.charm.backup._list_timelines() == dict[str, tuple[str, str]]()

        _execute_command.return_value = (
            0,
            '{".":{"type":"path"},"archive/test-stanza/14-1/00000002.history":{"type": "file","size": 32,"time": 1728937652}}',
            "",
        )
        assert harness.charm.backup._list_timelines() == dict[str, tuple[str, str]]([
            ("2024-10-14T20:27:32Z", ("test-stanza", "2"))
        ])


def test_get_nearest_timeline(harness):
    with (
        patch("charm.PostgreSQLBackups._list_backups") as _list_backups,
        patch("charm.PostgreSQLBackups._list_timelines") as _list_timelines,
    ):
        _list_backups.return_value = dict[str, tuple[str, str]]()
        _list_timelines.return_value = dict[str, tuple[str, str]]()
        assert harness.charm.backup._get_nearest_timeline("2022-02-24 05:00:00") is None

        _list_backups.return_value = dict[str, tuple[str, str]]({
            "2022-02-24T05:00:00Z": ("test-stanza", "1"),
            "2024-02-24T05:00:00Z": ("test-stanza", "2"),
        })
        _list_timelines.return_value = dict[str, tuple[str, str]]({
            "2023-02-24T05:00:00Z": ("test-stanza", "2")
        })
        assert harness.charm.backup._get_nearest_timeline("latest") == tuple[str, str]((
            "test-stanza",
            "2",
        ))
        assert harness.charm.backup._get_nearest_timeline("2025-01-01 00:00:00") == tuple[
            str, str
        ](("test-stanza", "2"))
        assert harness.charm.backup._get_nearest_timeline("2024-01-01 00:00:00") == tuple[
            str, str
        ](("test-stanza", "2"))
        assert harness.charm.backup._get_nearest_timeline("2023-01-01 00:00:00") == tuple[
            str, str
        ](("test-stanza", "1"))
        assert harness.charm.backup._get_nearest_timeline("2022-01-01 00:00:00") is None


def test_is_psql_timestamp(harness):
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00+0000") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00+03") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00-01:00") is True

    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.01") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.01+0000") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.01+03") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.01-01:00") is True

    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.500001") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.500001+0000") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.500001+03") is True
    assert harness.charm.backup._is_psql_timestamp("2022-02-24 05:00:00.500001-01:00") is True

    assert harness.charm.backup._is_psql_timestamp("bad data") is False
    assert harness.charm.backup._is_psql_timestamp("2022-02-24T05:00:00.5000001-01:00") is False
    assert harness.charm.backup._is_psql_timestamp("2022-24-24 05:00:00") is False


def test_on_restore_action(harness):
    with (
        patch("charm.Patroni.start_patroni") as _start_patroni,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgreSQLBackups._empty_data_files") as _empty_data_files,
        patch("charm.PostgreSQLBackups._restart_database") as _restart_database,
        patch("charm.PostgreSQLBackups._execute_command") as _execute_command,
        patch("charm.Patroni.stop_patroni") as _stop_patroni,
        patch("charm.PostgreSQLBackups._list_backups") as _list_backups,
        patch("charm.PostgreSQLBackups._list_timelines") as _list_timelines,
        patch("charm.PostgreSQLBackups._fetch_backup_from_id") as _fetch_backup_from_id,
        patch("charm.PostgreSQLBackups._pre_restore_checks") as _pre_restore_checks,
        patch(
            "charm.PostgresqlOperatorCharm.override_patroni_restart_condition"
        ) as _override_patroni_restart_condition,
        patch(
            "charm.PostgresqlOperatorCharm.restore_patroni_restart_condition"
        ) as _restore_patroni_restart_condition,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        # Test when pre restore checks fail.
        mock_event = MagicMock()
        _pre_restore_checks.return_value = False
        harness.charm.unit.status = ActiveStatus()
        harness.charm.backup._on_restore_action(mock_event)
        _list_backups.assert_not_called()
        _stop_patroni.assert_not_called()
        _execute_command.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_not_called()
        assert not isinstance(harness.charm.unit.status, MaintenanceStatus)

        # Test when the user provides an invalid backup id.
        mock_event.params = {"backup-id": "2023-01-01T10:00:00Z"}
        _pre_restore_checks.return_value = True
        _list_backups.return_value = {
            "2023-01-01T09:00:00Z": (harness.charm.backup.stanza_name, "1")
        }
        _list_timelines.return_value = {
            "2024-02-24T05:00:00Z": (harness.charm.backup.stanza_name, "2")
        }
        harness.charm.unit.status = ActiveStatus()
        harness.charm.backup._on_restore_action(mock_event)
        _list_backups.assert_called_once_with(show_failed=False)
        _list_timelines.assert_called_once()
        _fetch_backup_from_id.assert_not_called()
        mock_event.fail.assert_called_once()
        _stop_patroni.assert_not_called()
        _execute_command.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.set_results.assert_not_called()
        assert not isinstance(harness.charm.unit.status, MaintenanceStatus)

        # Test when the user provides an only the timeline backup id leading to the error.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2024-02-24T05:00:00Z"}
        harness.charm.unit.status = ActiveStatus()
        harness.charm.backup._on_restore_action(mock_event)
        _fetch_backup_from_id.assert_not_called()
        mock_event.fail.assert_called_once()
        _stop_patroni.assert_not_called()
        _execute_command.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.set_results.assert_not_called()
        assert not isinstance(harness.charm.unit.status, MaintenanceStatus)

        # Test when the charm fails to stop the workload.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        _stop_patroni.return_value = False
        harness.charm.backup._on_restore_action(mock_event)
        _stop_patroni.assert_called_once()
        mock_event.fail.assert_called_once()
        _execute_command.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to remove the files from the data directory.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        _stop_patroni.return_value = True
        _empty_data_files.return_value = False
        harness.charm.backup._on_restore_action(mock_event)
        _empty_data_files.assert_called_once()
        mock_event.fail.assert_called_once()
        _restart_database.assert_called_once()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to remove the previous cluster information.
        mock_event.reset_mock()
        _restart_database.reset_mock()
        _empty_data_files.return_value = True
        _execute_command.return_value = (1, "", "fake stderr")
        _fetch_backup_from_id.return_value = "20230101-090000F"
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}
        harness.charm.backup._on_restore_action(mock_event)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "restoring-backup": "20230101-090000F",
            "restore-stanza": f"{harness.charm.model.name}.{harness.charm.cluster_name}",
        }
        _execute_command.assert_called_once_with(
            [
                "charmed-postgresql.patronictl",
                "-c",
                "/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml",
                "remove",
                harness.charm.app.name,
            ],
            command_input=b"postgresql\nYes I am aware",
            timeout=10,
        )
        mock_event.fail.assert_called_once()
        _restart_database.assert_not_called()
        _update_config.assert_called_once()
        _start_patroni.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test a successful start of the restore process.
        mock_event.reset_mock()
        _restart_database.reset_mock()
        _execute_command.return_value = (0, "fake stdout", "")
        _fetch_backup_from_id.return_value = "20230101-090000F"
        harness.charm.backup._on_restore_action(mock_event)
        _restart_database.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"restore-status": "restore started"})

        # Test a successful PITR with the real backup id to the latest.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z", "restore-to-time": "latest"}
        _restart_database.reset_mock()
        _execute_command.return_value = (0, "fake stdout", "")
        _fetch_backup_from_id.return_value = "20230101-090000F"
        harness.charm.backup._on_restore_action(mock_event)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "restoring-backup": "20230101-090000F",
            "restore-timeline": "1",
            "restore-to-time": "latest",
            "restore-stanza": f"{harness.charm.model.name}.{harness.charm.cluster_name}",
        }
        _restart_database.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"restore-status": "restore started"})

        # Test a successful PITR with only the timestamp.
        mock_event.reset_mock()
        mock_event.params = {"restore-to-time": "2025-02-24 05:00:00.001+00"}
        _restart_database.reset_mock()
        _execute_command.return_value = (0, "fake stdout", "")
        _fetch_backup_from_id.return_value = "20230101-090000F"
        harness.charm.backup._on_restore_action(mock_event)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "restore-timeline": "2",
            "restore-to-time": "2025-02-24 05:00:00.001+00",
            "restore-stanza": f"{harness.charm.model.name}.{harness.charm.cluster_name}",
        }
        _restart_database.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"restore-status": "restore started"})

        # Test a failed PITR with only the restore-to-time parameter equal to latest
        # (it should fail when there is no base backup created from the latest timeline).
        mock_event.reset_mock()
        _empty_data_files.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                {
                    "restore-timeline": "",
                    "restore-to-time": "",
                    "restore-stanza": "",
                },
            )
        _update_config.reset_mock()
        mock_event.params = {"restore-to-time": "latest"}
        harness.charm.backup._on_restore_action(mock_event)
        _empty_data_files.assert_not_called()
        _restart_database.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}
        _update_config.assert_not_called()
        mock_event.set_results.assert_not_called()
        mock_event.fail.assert_called_once()

        # Test a successful PITR with only the restore-to-time parameter equal to latest.
        mock_event.reset_mock()
        mock_event.params = {"restore-to-time": "latest"}
        _list_backups.return_value = {
            "2023-01-01T09:00:00Z": (harness.charm.backup.stanza_name, "1"),
            "2024-02-24T05:00:00Z": (harness.charm.backup.stanza_name, "2"),
        }
        harness.charm.backup._on_restore_action(mock_event)
        _empty_data_files.assert_called_once()
        _restart_database.assert_not_called()
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {
            "restore-timeline": "2",
            "restore-to-time": "latest",
            "restore-stanza": f"{harness.charm.model.name}.{harness.charm.cluster_name}",
        }
        _update_config.assert_called_once()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"restore-status": "restore started"})


def test_pre_restore_checks(harness):
    with (
        patch("ops.model.Application.planned_units") as _planned_units,
        patch("charm.PostgreSQLBackups._are_backup_settings_ok") as _are_backup_settings_ok,
    ):
        # Test when S3 parameters are not ok.
        mock_event = MagicMock(params={})
        _are_backup_settings_ok.return_value = (False, "fake error message")
        assert not harness.charm.backup._pre_restore_checks(mock_event)
        mock_event.fail.assert_called_once()

        # Test when no backup id is provided.
        mock_event.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        assert not harness.charm.backup._pre_restore_checks(mock_event)
        mock_event.fail.assert_called_once()

        # Test when the unit is in a blocked state that is not recoverable by changing
        # S3 parameters.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        harness.charm.unit.status = BlockedStatus("fake blocked state")
        assert not harness.charm.backup._pre_restore_checks(mock_event)
        mock_event.fail.assert_called_once()

        # Test when the unit is in a blocked state that is recoverable by changing S3 parameters,
        # but the cluster has more than one unit.
        mock_event.reset_mock()
        harness.charm.unit.status = BlockedStatus(ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE)
        _planned_units.return_value = 2
        assert not harness.charm.backup._pre_restore_checks(mock_event)
        mock_event.fail.assert_called_once()

        # Test when the cluster has only one unit, but it's not the leader yet.
        mock_event.reset_mock()
        _planned_units.return_value = 1
        assert not harness.charm.backup._pre_restore_checks(mock_event)
        mock_event.fail.assert_called_once()

        # Test when everything is ok to run a restore.
        mock_event.reset_mock()
        with harness.hooks_disabled():
            harness.set_leader()
        assert harness.charm.backup._pre_restore_checks(mock_event)
        mock_event.fail.assert_not_called()


@pytest.mark.parametrize(
    "tls_ca_chain_filename",
    ["", "/var/snap/charmed-postgresql/common/pgbackrest-tls-ca-chain.crt"],
)
def test_render_pgbackrest_conf_file(harness, tls_ca_chain_filename):
    with (
        patch("charm.Patroni.render_file") as _render_file,
        patch(
            "charm.PostgreSQLBackups._tls_ca_chain_filename",
            new_callable=PropertyMock(return_value=tls_ca_chain_filename),
        ) as _tls_ca_chain_filename,
        patch("charm.PostgreSQLBackups._retrieve_s3_parameters") as _retrieve_s3_parameters,
    ):
        # Set up a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/pgbackrest.conf.j2") as f:
            mock = mock_open(read_data=f.read())

        # Test when there are missing S3 parameters.
        _retrieve_s3_parameters.return_value = [], ["bucket", "access-key", "secret-key"]

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            harness.charm.backup._render_pgbackrest_conf_file()

        mock.assert_not_called()
        _render_file.assert_not_called()

        # Test when all parameters are provided.
        _retrieve_s3_parameters.return_value = (
            {
                "bucket": "test-bucket",
                "access-key": "test-access-key",
                "secret-key": "test-secret-key",
                "endpoint": "https://storage.googleapis.com",
                "path": "test-path/",
                "region": "us-east-1",
                "s3-uri-style": "path",
                "delete-older-than-days": "30",
                "tls-ca-chain": (["fake-tls-ca-chain"] if tls_ca_chain_filename != "" else ""),
            },
            [],
        )

        # Get the expected content from a file.
        with open("templates/pgbackrest.conf.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            enable_tls=harness.charm.is_tls_enabled
            and len(harness.charm.peer_members_endpoints) > 0,
            peer_endpoints=harness.charm._peer_members_ips,
            path="test-path/",
            data_path="/var/snap/charmed-postgresql/common/var/lib/postgresql",
            log_path="/var/snap/charmed-postgresql/common/var/log/pgbackrest",
            region="us-east-1",
            endpoint="https://storage.googleapis.com",
            bucket="test-bucket",
            s3_uri_style="path",
            tls_ca_chain=(tls_ca_chain_filename or ""),
            access_key="test-access-key",
            secret_key="test-secret-key",
            stanza=harness.charm.backup.stanza_name,
            storage_path=harness.charm._storage_path,
            user="backup",
            retention_full=30,
            process_max=max(cpu_count() - 2, 1),
        )

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            harness.charm.backup._render_pgbackrest_conf_file()

        # Check the template is opened read-only in the call to open.
        assert mock.call_args_list[0][0] == ("templates/pgbackrest.conf.j2",)

        # Get the expected content from a file.
        with open("templates/pgbackrest.conf.j2") as file:
            template = Template(file.read())
        log_rotation_expected_content = template.render()

        # Ensure the correct rendered template is sent to _render_file method.
        calls = [
            call(
                "/var/snap/charmed-postgresql/current/etc/pgbackrest/pgbackrest.conf",
                expected_content,
                0o640,
            ),
            call(
                "/etc/logrotate.d/pgbackrest.logrotate",
                log_rotation_expected_content,
                0o644,
                change_owner=False,
            ),
        ]
        if tls_ca_chain_filename != "":
            calls.insert(0, call(tls_ca_chain_filename, "fake-tls-ca-chain", 0o644))
        _render_file.assert_has_calls(calls)


def test_restart_database(harness):
    with (
        patch("charm.Patroni.start_patroni") as _start_patroni,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        harness.charm.backup._restart_database()

        # Assert that the backup id is not in the application relation databag anymore.
        assert harness.get_relation_data(peer_rel_id, harness.charm.app) == {}

        _update_config.assert_called_once()
        _start_patroni.assert_called_once()


def test_retrieve_s3_parameters(harness):
    with patch(
        "charms.data_platform_libs.v0.s3.S3Requirer.get_s3_connection_info"
    ) as _get_s3_connection_info:
        # Test when there are missing S3 parameters.
        _get_s3_connection_info.return_value = {}
        assert harness.charm.backup._retrieve_s3_parameters() == (
            {},
            ["bucket", "access-key", "secret-key"],
        )

        # Test when only the required parameters are provided.
        _get_s3_connection_info.return_value = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
        }
        assert harness.charm.backup._retrieve_s3_parameters() == (
            {
                "access-key": "test-access-key",
                "bucket": "test-bucket",
                "delete-older-than-days": "9999999",
                "endpoint": "https://s3.amazonaws.com",
                "path": "/",
                "s3-uri-style": "host",
                "secret-key": "test-secret-key",
            },
            [],
        )

        # Test when all parameters are provided.
        _get_s3_connection_info.return_value = {
            "bucket": " /test-bucket/ ",
            "access-key": " test-access-key ",
            "secret-key": " test-secret-key ",
            "endpoint": " https://storage.googleapis.com// ",
            "path": " test-path/ ",
            "region": " us-east-1 ",
            "s3-uri-style": " path ",
            "delete-older-than-days": "30",
        }
        assert harness.charm.backup._retrieve_s3_parameters() == (
            {
                "access-key": "test-access-key",
                "bucket": "test-bucket",
                "endpoint": "https://storage.googleapis.com",
                "path": "/test-path",
                "region": "us-east-1",
                "s3-uri-style": "path",
                "secret-key": "test-secret-key",
                "delete-older-than-days": "30",
            },
            [],
        )


def test_start_stop_pgbackrest_service(harness):
    with (
        patch(
            "charm.PostgreSQLBackups._is_primary_pgbackrest_service_running",
            new_callable=PropertyMock,
        ) as _is_primary_pgbackrest_service_running,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
        patch("charm.Patroni.get_standby_leader") as _get_standby_leader,
        patch("backups.snap.SnapCache") as _snap_cache,
        patch(
            "charm.PostgresqlOperatorCharm._peer_members_ips", new_callable=PropertyMock
        ) as _peer_members_ips,
        patch(
            "charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock
        ) as _is_tls_enabled,
        patch(
            "charm.PostgreSQLBackups._render_pgbackrest_conf_file"
        ) as _render_pgbackrest_conf_file,
        patch("charm.PostgreSQLBackups._are_backup_settings_ok") as _are_backup_settings_ok,
    ):
        # Test when S3 parameters are not ok (no operation, but returns success).
        _are_backup_settings_ok.return_value = (False, "fake error message")
        restart = MagicMock()
        stop = MagicMock()
        _snap_cache.return_value = {"charmed-postgresql": MagicMock(restart=restart, stop=stop)}
        assert harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_not_called()
        restart.assert_not_called()

        # Test when it was not possible to render the pgBackRest configuration file.
        _are_backup_settings_ok.return_value = (True, None)
        _render_pgbackrest_conf_file.return_value = False
        assert not harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_not_called()
        restart.assert_not_called()

        # Test when TLS is not enabled (should stop the service).
        _render_pgbackrest_conf_file.return_value = True
        _is_tls_enabled.return_value = False
        assert harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_called_once()
        restart.assert_not_called()

        # Test when there are no replicas.
        stop.reset_mock()
        _is_tls_enabled.return_value = True
        _peer_members_ips.return_value = []
        assert harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_called_once()
        restart.assert_not_called()

        # Test when it's a standby.
        stop.reset_mock()
        _peer_members_ips.return_value = ["1.1.1.1"]
        _get_standby_leader.return_value = "standby"
        assert harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_called_once()
        restart.assert_not_called()

        # Test when the service hasn't started in the primary yet.
        stop.reset_mock()
        _get_standby_leader.return_value = None
        _is_primary.return_value = False
        _is_primary_pgbackrest_service_running.return_value = False
        assert not harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_not_called()
        restart.assert_not_called()

        # Test when the service has already started in the primary.
        _is_primary_pgbackrest_service_running.return_value = True
        assert harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_not_called()
        restart.assert_called_once()

        # Test when this unit is the primary.
        restart.reset_mock()
        _is_primary.return_value = True
        _is_primary_pgbackrest_service_running.return_value = False
        assert harness.charm.backup.start_stop_pgbackrest_service()
        stop.assert_not_called()
        restart.assert_called_once()


@pytest.mark.parametrize(
    "tls_ca_chain_filename",
    ["", "/var/snap/charmed-postgresql/common/pgbackrest-tls-ca-chain.crt"],
)
def test_upload_content_to_s3(harness, tls_ca_chain_filename):
    with (
        patch("tempfile.NamedTemporaryFile") as _named_temporary_file,
        patch("charm.PostgreSQLBackups._construct_endpoint") as _construct_endpoint,
        patch("boto3.session.Session.resource") as _resource,
        patch("backups.Config") as _config,
        patch(
            "charm.PostgreSQLBackups._tls_ca_chain_filename",
            new_callable=PropertyMock(return_value=tls_ca_chain_filename),
        ) as _tls_ca_chain_filename,
    ):
        # Set some parameters.
        content = "test-content"
        s3_path = "test-file."
        s3_parameters = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "endpoint": "https://s3.amazonaws.com",
            "path": "/test-path",
            "region": "us-east-1",
        }

        # Test when any exception happens.
        upload_file = _resource.return_value.Bucket.return_value.upload_file
        _resource.side_effect = ValueError
        _construct_endpoint.return_value = "https://s3.us-east-1.amazonaws.com"
        _named_temporary_file.return_value.__enter__.return_value.name = "/tmp/test-file"
        assert not harness.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters)
        _resource.assert_called_once_with(
            "s3",
            endpoint_url="https://s3.us-east-1.amazonaws.com",
            verify=(tls_ca_chain_filename or None),
            config=_config.return_value,
        )
        _config.assert_called_once_with(
            # https://github.com/boto/boto3/issues/4400#issuecomment-2600742103
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        _named_temporary_file.assert_not_called()
        upload_file.assert_not_called()

        _resource.reset_mock()
        _config.reset_mock()
        _resource.side_effect = None
        upload_file.side_effect = S3UploadFailedError
        assert not harness.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters)
        _resource.assert_called_once_with(
            "s3",
            endpoint_url="https://s3.us-east-1.amazonaws.com",
            verify=(tls_ca_chain_filename or None),
            config=_config.return_value,
        )
        _config.assert_called_once_with(
            # https://github.com/boto/boto3/issues/4400#issuecomment-2600742103
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        _named_temporary_file.assert_called_once()
        upload_file.assert_called_once_with("/tmp/test-file", "test-path/test-file.")

        # Test when the upload succeeds
        _resource.reset_mock()
        _config.reset_mock()
        _named_temporary_file.reset_mock()
        upload_file.reset_mock()
        upload_file.side_effect = None
        assert harness.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters)
        _resource.assert_called_once_with(
            "s3",
            endpoint_url="https://s3.us-east-1.amazonaws.com",
            verify=(tls_ca_chain_filename or None),
            config=_config.return_value,
        )
        _config.assert_called_once_with(
            # https://github.com/boto/boto3/issues/4400#issuecomment-2600742103
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        _named_temporary_file.assert_called_once()
        upload_file.assert_called_once_with("/tmp/test-file", "test-path/test-file.")
