# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import MagicMock, PropertyMock, call, patch

from botocore.exceptions import ClientError
from ops import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.testing import Harness

from backups import ListBackupsError
from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
S3_PARAMETERS_RELATION = "s3-parameters"


class TestPostgreSQLBackups(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "postgresql")
        self.harness.add_relation_unit(self.peer_rel_id, "postgresql/0")
        self.harness.begin()
        self.charm = self.harness.charm

    def relate_to_s3_integrator(self):
        self.s3_rel_id = self.harness.add_relation(S3_PARAMETERS_RELATION, "s3-integrator")

    def remove_relation_from_s3_integrator(self):
        self.harness.remove_relation(S3_PARAMETERS_RELATION, "s3-integrator")
        self.s3_rel_id = None

    @patch("charm.PostgreSQLBackups.start_stop_pgbackrest_service")
    @patch("charm.PostgreSQLBackups._initialise_stanza")
    @patch("charm.PostgreSQLBackups.can_use_s3_repository")
    @patch("charm.PostgreSQLBackups._create_bucket_if_not_exists")
    @patch("charm.PostgreSQLBackups._render_pgbackrest_conf_file")
    @patch("ops.framework.EventBase.defer")
    def test_on_s3_credential_changed(
        self,
        _defer,
        _render_pgbackrest_conf_file,
        _create_bucket_if_not_exists,
        _can_use_s3_repository,
        _initialise_stanza,
        _start_stop_pgbackrest_service,
    ):
        # Test when the cluster was not initialised yet.
        self.relate_to_s3_integrator()
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _defer.assert_called_once()
        _render_pgbackrest_conf_file.assert_not_called()
        _create_bucket_if_not_exists.assert_not_called()
        _can_use_s3_repository.assert_not_called()
        _initialise_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the cluster is already initialised, but the charm fails to render
        # the pgBackRest configuration file due to missing S3 parameters.
        _defer.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"cluster_initialised": "True"},
            )
        _render_pgbackrest_conf_file.return_value = False
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _defer.assert_not_called()
        _render_pgbackrest_conf_file.assert_called_once()
        _create_bucket_if_not_exists.assert_not_called()
        _can_use_s3_repository.assert_not_called()
        _initialise_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the charm render the pgBackRest configuration file, but fails to
        # access or create the S3 bucket.
        for error in [
            ClientError(
                error_response={"Error": {"Code": 1, "message": "fake error"}},
                operation_name="fake operation name",
            ),
            ValueError,
        ]:
            self.charm.unit.status = ActiveStatus()
            _render_pgbackrest_conf_file.reset_mock()
            _create_bucket_if_not_exists.reset_mock()
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.peer_rel_id,
                    self.charm.app.name,
                    {"cluster_initialised": "True"},
                )
            _render_pgbackrest_conf_file.return_value = True
            _create_bucket_if_not_exists.side_effect = error
            self.charm.backup.s3_client.on.credentials_changed.emit(
                relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
            )
            _render_pgbackrest_conf_file.assert_called_once()
            _create_bucket_if_not_exists.assert_called_once()
            self.assertIsInstance(self.charm.unit.status, BlockedStatus)
            self.assertEqual(
                self.charm.unit.status.message, FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE
            )
            _can_use_s3_repository.assert_not_called()
            _initialise_stanza.assert_not_called()
            _start_stop_pgbackrest_service.assert_not_called()

        # Test when it's not possible to use the S3 repository due to backups from another cluster.
        _create_bucket_if_not_exists.reset_mock()
        _create_bucket_if_not_exists.side_effect = None
        _can_use_s3_repository.return_value = (False, "fake validation message")
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)
        self.assertEqual(self.charm.unit.status.message, "fake validation message")
        _create_bucket_if_not_exists.assert_called_once()
        _can_use_s3_repository.assert_called_once()
        _initialise_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the stanza can be initialised and the pgBackRest service can start.
        _can_use_s3_repository.reset_mock()
        _can_use_s3_repository.return_value = (True, None)
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _can_use_s3_repository.assert_called_once()
        _initialise_stanza.assert_called_once()
        _start_stop_pgbackrest_service.assert_called_once()

    @patch("charm.PostgreSQLBackups._change_connectivity_to_database")
    @patch("charm.PostgreSQLBackups._list_backups")
    @patch("charm.PostgreSQLBackups._execute_command")
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    @patch("charm.PostgreSQLBackups._upload_content_to_s3")
    @patch("backups.datetime")
    @patch("ops.JujuVersion.from_environ")
    @patch("charm.PostgreSQLBackups._retrieve_s3_parameters")
    @patch("charm.PostgreSQLBackups._can_unit_perform_backup")
    def test_on_create_backup_action(
        self,
        _can_unit_perform_backup,
        _retrieve_s3_parameters,
        _from_environ,
        _datetime,
        _upload_content_to_s3,
        _is_primary,
        _execute_command,
        _list_backups,
        _change_connectivity_to_database,
    ):
        # Test when the unit cannot perform a backup.
        mock_event = MagicMock()
        _can_unit_perform_backup.return_value = (False, "fake validation message")
        self.charm.backup._on_create_backup_action(mock_event)
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
Model Name: {self.charm.model.name}
Application Name: {self.charm.model.app.name}
Unit Name: {self.charm.unit.name}
Juju Version: test-juju-version
"""
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_called_once_with(
            expected_metadata,
            f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
            mock_s3_parameters,
        )
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup fails.
        mock_event.reset_mock()
        _upload_content_to_s3.return_value = True
        _is_primary.return_value = True
        _execute_command.return_value = (1, "", "fake error")
        self.charm.backup._on_create_backup_action(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup succeeds but the charm fails to upload the backup logs.
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _upload_content_to_s3.side_effect = [True, False]
        _execute_command.side_effect = None
        _execute_command.return_value = (0, "fake stdout", "fake stderr")
        _list_backups.return_value = {"2023-01-01T09:00:00Z": self.charm.backup.stanza_name}
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls(
            [
                call(
                    expected_metadata,
                    f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
                    mock_s3_parameters,
                ),
                call(
                    "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                    f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                    mock_s3_parameters,
                ),
            ]
        )
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup succeeds (including the upload of the backup logs).
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _upload_content_to_s3.side_effect = None
        _upload_content_to_s3.return_value = True
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls(
            [
                call(
                    expected_metadata,
                    f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
                    mock_s3_parameters,
                ),
                call(
                    "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                    f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                    mock_s3_parameters,
                ),
            ]
        )
        _change_connectivity_to_database.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once()

        # Test when this unit is a replica (the connectivity to the database should be changed).
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _is_primary.return_value = False
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls(
            [
                call(
                    expected_metadata,
                    f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
                    mock_s3_parameters,
                ),
                call(
                    "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                    f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                    mock_s3_parameters,
                ),
            ]
        )
        self.assertEqual(_change_connectivity_to_database.call_count, 2)
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"backup-status": "backup created"})

    @patch("charm.PostgreSQLBackups._generate_backup_list_output")
    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    def test_on_list_backups_action(self, _are_backup_settings_ok, _generate_backup_list_output):
        # Test when not all backup settings are ok.
        mock_event = MagicMock()
        _are_backup_settings_ok.return_value = (False, "fake validation message")
        self.charm.backup._on_list_backups_action(mock_event)
        mock_event.fail.assert_called_once()
        _generate_backup_list_output.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to generate the backup list output.
        mock_event.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        _generate_backup_list_output.side_effect = ListBackupsError
        self.charm.backup._on_list_backups_action(mock_event)
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
2023-01-01T09:00:00Z  | physical     | failed: fake error
2023-01-01T10:00:00Z  | physical     | finished"""
        self.charm.backup._on_list_backups_action(mock_event)
        _generate_backup_list_output.assert_called_once()
        mock_event.set_results.assert_called_once_with(
            {
                "backups": """backup-id             | backup-type  | backup-status
----------------------------------------------------
2023-01-01T09:00:00Z  | physical     | failed: fake error
2023-01-01T10:00:00Z  | physical     | finished"""
            }
        )
        mock_event.fail.assert_not_called()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.start_patroni")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgreSQLBackups._empty_data_files")
    @patch("charm.PostgreSQLBackups._restart_database")
    @patch("charm.PostgreSQLBackups._execute_command")
    @patch("charm.Patroni.stop_patroni")
    @patch("charm.PostgreSQLBackups._list_backups")
    @patch("charm.PostgreSQLBackups._pre_restore_checks")
    def test_on_restore_action(
        self,
        _pre_restore_checks,
        _list_backups,
        _stop_patroni,
        _execute_command,
        _restart_database,
        _empty_data_files,
        _update_config,
        _start_patroni,
    ):
        # Test when pre restore checks fail.
        mock_event = MagicMock()
        _pre_restore_checks.return_value = False
        self.charm.unit.status = ActiveStatus()
        self.charm.backup._on_restore_action(mock_event)
        _list_backups.assert_not_called()
        _stop_patroni.assert_not_called()
        _execute_command.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_not_called()
        self.assertNotIsInstance(self.charm.unit.status, MaintenanceStatus)

        # Test when the user provides an invalid backup id.
        mock_event.params = {"backup-id": "2023-01-01T10:00:00Z"}
        _pre_restore_checks.return_value = True
        _list_backups.return_value = {"2023-01-01T09:00:00Z": self.charm.backup.stanza_name}
        self.charm.unit.status = ActiveStatus()
        self.charm.backup._on_restore_action(mock_event)
        _list_backups.assert_called_once_with(show_failed=False)
        mock_event.fail.assert_called_once()
        _stop_patroni.assert_not_called()
        _execute_command.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        mock_event.set_results.assert_not_called()
        self.assertNotIsInstance(self.charm.unit.status, MaintenanceStatus)

        # Test when the charm fails to stop the workload.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        _stop_patroni.return_value = False
        self.charm.backup._on_restore_action(mock_event)
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
        self.charm.backup._on_restore_action(mock_event)
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
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.charm.backup._on_restore_action(mock_event)
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {
                "restoring-backup": "20230101-090000F",
                "restore-stanza": f"{self.charm.model.name}.{self.charm.cluster_name}",
            },
        )
        _execute_command.assert_called_once_with(
            [
                "charmed-postgresql.patronictl",
                "-c",
                "/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml",
                "remove",
                self.charm.app.name,
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
        self.charm.backup._on_restore_action(mock_event)
        _restart_database.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"restore-status": "restore started"})
