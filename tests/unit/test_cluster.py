# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import os
import unittest
from unittest.mock import call, mock_open, patch

from jinja2 import Template

from cluster import (
    ClusterAlreadyRunningError,
    ClusterNotRunningError,
    PostgresqlCluster,
)
from lib.charms.operator_libs_linux.v0.apt import DebianPackage, PackageState
from tests.helpers import STORAGE_PATH

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"
PATRONI_SERVICE = "patroni"


class TestCharm(unittest.TestCase):
    def setUp(self):
        # Setup a cluster.
        self.cluster = PostgresqlCluster("1.1.1.1")

    @patch("charm.PostgresqlCluster._start_cluster")
    @patch("charm.PostgresqlCluster._render_postgresql_conf_file")
    @patch("charm.PostgresqlCluster._copy_pg_hba_conf_file")
    @patch("charm.PostgresqlCluster._create_cluster")
    @patch("charm.PostgresqlCluster._is_cluster_running")
    def test_bootstrap_cluster(
        self,
        _is_cluster_running,
        _create_cluster,
        _copy_pg_hba_conf_file,
        _render_postgresql_conf_file,
        _start_cluster,
    ):
        password = "random-password"

        # Set the return value for the _is_cluster_running method to test the three scenarios
        # (True to throw ClusterAlreadyRunningError, False and False to throw
        # ClusterNotRunningError, False and True to succeed).
        _is_cluster_running.side_effect = [True, False, False, False, True]

        # Test the cluster already running and not running errors.
        with self.assertRaises(ClusterAlreadyRunningError):
            self.cluster.bootstrap_cluster(password)
        with self.assertRaises(ClusterNotRunningError):
            self.cluster.bootstrap_cluster(password)
            _create_cluster.assert_called_once_with(password)
            _copy_pg_hba_conf_file.assert_called_once()
            _render_postgresql_conf_file.assert_called_once()
            _start_cluster.assert_called_once()

        # Reset the call count of the mocks.
        _create_cluster.reset_mock()
        _copy_pg_hba_conf_file.reset_mock()
        _render_postgresql_conf_file.reset_mock()
        _start_cluster.reset_mock()

        # Then test the working bootstrap process.
        self.cluster.bootstrap_cluster(password)
        _create_cluster.assert_called_once_with(password)
        _copy_pg_hba_conf_file.assert_called_once()
        _render_postgresql_conf_file.assert_called_once()
        _start_cluster.assert_called_once()

    @patch("charm.PostgresqlCluster._change_owner")
    @patch("os.chmod")
    @patch("os.makedirs")
    def test_create_directory(self, _makedirs, _chmod, _change_owner):
        path = "/tmp/fakedir"
        mode = 0o755

        self.cluster._create_directory(path, mode)
        _makedirs.assert_called_once_with(path, mode=mode, exist_ok=True)
        _chmod.assert_called_once_with(path, mode)
        _change_owner.assert_called_once_with(path)

    @patch("os.makedirs")
    def test_inhibit_default_cluster_creation(self, _makedirs):
        # Setup a mock for the `open` method.
        mock = mock_open()
        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            self.cluster.inhibit_default_cluster_creation()
            _makedirs.assert_called_once_with(
                os.path.dirname(CREATE_CLUSTER_CONF_PATH), mode=0o755, exist_ok=True
            )
            # Check the write calls made to the file.
            handle = mock()
            calls = [
                call("create_main_cluster = false\n"),
                call(f"include '{self.cluster.conf_path}/conf.d/postgresql-operator.conf'"),
            ]
            handle.write.assert_has_calls(calls)

    @patch("shutil.copyfile")
    def test_copy_pg_hba_conf_file(self, _copyfile):
        # Call the method.
        self.cluster._copy_pg_hba_conf_file()
        # Ensure the copyfile command was called with the right paths.
        _copyfile.assert_called_once_with(
            "src/pg_hba.conf", f"{self.cluster.conf_path}/pg_hba.conf"
        )

    @patch("os.remove")
    @patch("subprocess.call")
    @patch("os.chown")
    @patch("pwd.getpwnam")
    @patch("tempfile.NamedTemporaryFile")
    def test_create_cluster(self, _temp_file, _pwnam, _chown, _call, _remove):
        # Set a mocked temporary filename.
        filename = "/tmp/temporaryfilename"
        _temp_file.return_value.name = filename
        # Define the arguments that 'check_call' should be called with.
        args = [
            "pg_createcluster",
            self.cluster.version,
            "main",
            "--datadir=/var/lib/postgresql/data/pgdata",
            "--",
            f"--pwfile={filename}",
        ]
        # Define a random password to be passed to the command.
        password = "random-password"
        # Successful command execution returns 0.
        _call.return_value = 0

        # Setup a mock for the `open` method.
        mock = mock_open()
        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Set the uid/gid return values for lookup of 'postgres' user.
            _pwnam.return_value.pw_uid = 35
            _pwnam.return_value.pw_gid = 35
            # Call the method.
            self.cluster._create_cluster(password)

        # Assert the correct ownership of the pwfile.
        _pwnam.assert_called_with("postgres")
        _chown.assert_called_once_with(filename, uid=35, gid=35)
        # Check that check_call was invoked with the correct
        # arguments and the pwfile is removed in the end.
        _call.assert_called_once_with(args)
        _remove.assert_called_once_with(filename)

    @patch("charms.operator_libs_linux.v0.apt.DebianPackage.from_system")
    def test_get_postgresql_version(self, _from_system):
        # Mock the package returned by from_system call.
        _from_system.return_value = DebianPackage(
            "postgresql", "12+214ubuntu0.1", "", "all", PackageState.Present
        )
        version = self.cluster._get_postgresql_version()
        _from_system.assert_called_once_with("postgresql")
        self.assertEqual(version, "12")

    @patch("subprocess.check_output")
    def test_is_cluster_running(self, _check_output):
        # Successful command execution returns no running clusters.
        _check_output.return_value = b""
        # Execute the method and check that there is no running clusters.
        result = self.cluster._is_cluster_running()
        self.assertEqual(result, False)
        # Change to return one running cluster.
        _check_output.return_value = b"12 main 5432 online postgres /var/lib/postgresql/12/main /var/log/postgresql/postgresql-12-main.log\n"
        # Then check that there is a running cluster.
        result = self.cluster._is_cluster_running()
        self.assertEqual(result, True)
        # Check that check_call was invoked with the correct arguments.
        _check_output.assert_called_with(["pg_lsclusters", "--no-header"])

    @patch("os.chmod")
    @patch("os.chown")
    @patch("pwd.getpwnam")
    @patch("tempfile.NamedTemporaryFile")
    def test_render_file(self, _temp_file, _pwnam, _chown, _chmod):
        # Set a mocked temporary filename.
        filename = "/tmp/temporaryfilename"
        _temp_file.return_value.name = filename
        # Setup a mock for the `open` method.
        mock = mock_open()
        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Set the uid/gid return values for lookup of 'postgres' user.
            _pwnam.return_value.pw_uid = 35
            _pwnam.return_value.pw_gid = 35
            # Call the method using a temporary configuration file.
            self.cluster._render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        self.assertEqual(mock.call_args_list[0][0], (filename, "w+"))
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("postgres")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

    @patch("charm.PostgresqlCluster._render_file")
    @patch("charm.PostgresqlCluster._create_directory")
    def test_render_patroni_service_file(self, _, _render_file):
        # Get the expected content from a file.
        with open("templates/patroni.service.j2") as file:
            template = Template(file.read())
        expected_content = template.render(conf_path=STORAGE_PATH)

        # Setup a mock for the `open` method, set returned data to patroni.service template.
        with open("templates/patroni.service.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.cluster._render_patroni_service_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.service.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            "/etc/systemd/system/patroni.service",
            expected_content,
            0o644,
        )

    @patch("charm.PostgresqlCluster._render_file")
    @patch("charm.PostgresqlCluster._create_directory")
    def test_render_patroni_yml_file(self, _, _render_file):
        # Define variables to render in the template.
        member_name = "postgresql-0"
        scope = "postgresql"
        superuser_password = "fake-superuser-password"
        replication_password = "fake-replication-password"

        # Get the expected content from a file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            conf_path=STORAGE_PATH,
            member_name=member_name,
            scope=scope,
            self_ip=self.cluster.unit_ip,
            superuser_password=superuser_password,
            replication_password=replication_password,
            version=self.cluster._get_postgresql_version(),
        )

        # Setup a mock for the `open` method, set returned data to patroni.yml template.
        with open("templates/patroni.yml.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method.
            self.cluster._render_patroni_yml_file(
                scope,
                member_name,
                superuser_password,
                replication_password,
            )

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.yml.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content,
            0o644,
        )

    @patch("charm.PostgresqlCluster._render_file")
    @patch("charm.PostgresqlCluster._create_directory")
    def test_render_postgresql_conf_file(self, _, _render_file):
        # Get the expected content from a file.
        with open("tests/data/postgresql.conf") as file:
            expected_content = file.read()

        # Setup a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/postgresql.conf.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.cluster._render_postgresql_conf_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/postgresql.conf.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{self.cluster.conf_path}/conf.d/postgresql-operator.conf",
            expected_content,
            0o644,
        )

    @patch("subprocess.call")
    def test_start_cluster(self, _call):
        # Successful command execution returns 0.
        _call.return_value = 0
        # Execute the method.
        self.cluster._start_cluster()
        # Check that check_call was invoked with the correct arguments.
        _call.assert_called_once_with(["pg_ctlcluster", self.cluster.version, "main", "start"])

    @patch("cluster.service_start")
    @patch("cluster.service_running")
    @patch("charm.PostgresqlCluster._create_directory")
    def test_start_patroni(self, _create_directory, _service_running, _service_start):
        _service_running.side_effect = [True, False]

        # Test a success scenario.
        success = self.cluster._start_patroni()
        _service_start.assert_called_with(PATRONI_SERVICE)
        _service_running.assert_called_with(PATRONI_SERVICE)
        assert success

        # Test a fail scenario.
        success = self.cluster._start_patroni()
        assert not success
