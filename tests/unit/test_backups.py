# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import MagicMock, PropertyMock, mock_open, patch

from boto3.exceptions import S3UploadFailedError
from jinja2 import Template
from ops import BlockedStatus
from ops.testing import Harness

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

    @patch("ops.model.Application.planned_units")
    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    def test_pre_restore_checks(self, _are_backup_settings_ok, _planned_units):
        # Test when S3 parameters are not ok.
        mock_event = MagicMock(params={})
        _are_backup_settings_ok.return_value = (False, "fake error message")
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when no backup id is provided.
        mock_event.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the unit is in a blocked state that is not recoverable by changing
        # S3 parameters.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        self.charm.unit.status = BlockedStatus("fake blocked state")
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the unit is in a blocked state that is recoverable by changing S3 parameters,
        # but the cluster has more than one unit.
        mock_event.reset_mock()
        self.charm.unit.status = BlockedStatus(ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE)
        _planned_units.return_value = 2
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the cluster has only one unit, but it's not the leader yet.
        mock_event.reset_mock()
        _planned_units.return_value = 1
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when everything is ok to run a restore.
        mock_event.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), True)
        mock_event.fail.assert_not_called()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.render_file")
    @patch("charm.PostgreSQLBackups._retrieve_s3_parameters")
    def test_render_pgbackrest_conf_file(self, _retrieve_s3_parameters, _render_file):
        # Set up a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/pgbackrest.conf.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Test when there are missing S3 parameters.
        _retrieve_s3_parameters.return_value = [], ["bucket", "access-key", "secret-key"]

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.charm.backup._render_pgbackrest_conf_file()

        mock.assert_not_called()
        _render_file.assert_not_called()

        # Test when all parameters are provided.
        _retrieve_s3_parameters.return_value = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "endpoint": "https://storage.googleapis.com",
            "path": "test-path/",
            "region": "us-east-1",
            "s3-uri-style": "path",
        }, []

        # Get the expected content from a file.
        with open("templates/pgbackrest.conf.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            enable_tls=self.charm.is_tls_enabled and len(self.charm.peer_members_endpoints) > 0,
            peer_endpoints=self.charm._peer_members_ips,
            path="test-path/",
            data_path="/var/snap/charmed-postgresql/common/var/lib/postgresql",
            log_path="/var/snap/charmed-postgresql/common/var/log/pgbackrest",
            region="us-east-1",
            endpoint="https://storage.googleapis.com",
            bucket="test-bucket",
            s3_uri_style="path",
            access_key="test-access-key",
            secret_key="test-secret-key",
            stanza=self.charm.backup.stanza_name,
            storage_path=self.charm._storage_path,
            user="backup",
        )

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.charm.backup._render_pgbackrest_conf_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/pgbackrest.conf.j2", "r"))

        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            "/var/snap/charmed-postgresql/current/etc/pgbackrest/pgbackrest.conf",
            expected_content,
            0o644,
        )

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.start_patroni")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    def test_restart_database(self, _update_config, _start_patroni):
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        self.charm.backup._restart_database()

        # Assert that the backup id is not in the application relation databag anymore.
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})

        _update_config.assert_called_once()
        _start_patroni.assert_called_once()

    @patch("charms.data_platform_libs.v0.s3.S3Requirer.get_s3_connection_info")
    def test_retrieve_s3_parameters(self, _get_s3_connection_info):
        # Test when there are missing S3 parameters.
        _get_s3_connection_info.return_value = {}
        self.assertEqual(
            self.charm.backup._retrieve_s3_parameters(),
            ({}, ["bucket", "access-key", "secret-key"]),
        )

        # Test when only the required parameters are provided.
        _get_s3_connection_info.return_value = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
        }
        self.assertEqual(
            self.charm.backup._retrieve_s3_parameters(),
            (
                {
                    "access-key": "test-access-key",
                    "bucket": "test-bucket",
                    "endpoint": "https://s3.amazonaws.com",
                    "path": "/",
                    "region": None,
                    "s3-uri-style": "host",
                    "secret-key": "test-secret-key",
                },
                [],
            ),
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
        }
        self.assertEqual(
            self.charm.backup._retrieve_s3_parameters(),
            (
                {
                    "access-key": "test-access-key",
                    "bucket": "test-bucket",
                    "endpoint": "https://storage.googleapis.com",
                    "path": "/test-path",
                    "region": "us-east-1",
                    "s3-uri-style": "path",
                    "secret-key": "test-secret-key",
                },
                [],
            ),
        )

    @patch(
        "charm.PostgreSQLBackups._is_primary_pgbackrest_service_running", new_callable=PropertyMock
    )
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    @patch("backups.snap.SnapCache")
    @patch("charm.PostgresqlOperatorCharm._peer_members_ips", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock)
    @patch("charm.PostgreSQLBackups._render_pgbackrest_conf_file")
    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    def test_start_stop_pgbackrest_service(
        self,
        _are_backup_settings_ok,
        _render_pgbackrest_conf_file,
        _is_tls_enabled,
        _peer_members_ips,
        _snap_cache,
        _is_primary,
        _is_primary_pgbackrest_service_running,
    ):
        # Test when S3 parameters are not ok (no operation, but returns success).
        _are_backup_settings_ok.return_value = (False, "fake error message")
        restart = MagicMock()
        stop = MagicMock()
        _snap_cache.return_value = {"charmed-postgresql": MagicMock(restart=restart, stop=stop)}
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        stop.assert_not_called()
        restart.assert_not_called()

        # Test when it was not possible to render the pgBackRest configuration file.
        _are_backup_settings_ok.return_value = (True, None)
        _render_pgbackrest_conf_file.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            False,
        )
        stop.assert_not_called()
        restart.assert_not_called()

        # Test when TLS is not enabled (should stop the service).
        _render_pgbackrest_conf_file.return_value = True
        _is_tls_enabled.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        stop.assert_called_once()
        restart.assert_not_called()

        # Test when there are no replicas.
        stop.reset_mock()
        _is_tls_enabled.return_value = True
        _peer_members_ips.return_value = []
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        stop.assert_called_once()
        restart.assert_not_called()

        # Test when the service hasn't started in the primary yet.
        stop.reset_mock()
        _peer_members_ips.return_value = ["1.1.1.1"]
        _is_primary.return_value = False
        _is_primary_pgbackrest_service_running.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            False,
        )
        stop.assert_not_called()
        restart.assert_not_called()

        # Test when the service has already started in the primary.
        _is_primary_pgbackrest_service_running.return_value = True
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        stop.assert_not_called()
        restart.assert_called_once()

        # Test when this unit is the primary.
        restart.reset_mock()
        _is_primary.return_value = True
        _is_primary_pgbackrest_service_running.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        stop.assert_not_called()
        restart.assert_called_once()

    @patch("tempfile.NamedTemporaryFile")
    @patch("charm.PostgreSQLBackups._construct_endpoint")
    @patch("boto3.session.Session.resource")
    def test_upload_content_to_s3(self, _resource, _construct_endpoint, _named_temporary_file):
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
        self.assertEqual(
            self.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters),
            False,
        )
        _resource.assert_called_once_with("s3", endpoint_url="https://s3.us-east-1.amazonaws.com")
        _named_temporary_file.assert_not_called()
        upload_file.assert_not_called()

        _resource.reset_mock()
        _resource.side_effect = None
        upload_file.side_effect = S3UploadFailedError
        self.assertEqual(
            self.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters),
            False,
        )
        _resource.assert_called_once_with("s3", endpoint_url="https://s3.us-east-1.amazonaws.com")
        _named_temporary_file.assert_called_once()
        upload_file.assert_called_once_with("/tmp/test-file", "test-path/test-file.")

        # Test when the upload succeeds
        _resource.reset_mock()
        _named_temporary_file.reset_mock()
        upload_file.reset_mock()
        upload_file.side_effect = None
        self.assertEqual(
            self.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters),
            True,
        )
        _resource.assert_called_once_with("s3", endpoint_url="https://s3.us-east-1.amazonaws.com")
        _named_temporary_file.assert_called_once()
        upload_file.assert_called_once_with("/tmp/test-file", "test-path/test-file.")
