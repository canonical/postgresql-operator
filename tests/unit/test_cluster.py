# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import mock_open, patch

from jinja2 import Template

from cluster import Patroni
from lib.charms.operator_libs_linux.v0.apt import DebianPackage, PackageState
from tests.helpers import STORAGE_PATH

PATRONI_SERVICE = "patroni"


class TestCharm(unittest.TestCase):
    def setUp(self):
        # Setup a cluster.
        self.peers_ips = peers_ips = ["2.2.2.2", "3.3.3.3"]

        self.patroni = Patroni(
            "1.1.1.1",
            STORAGE_PATH,
            "postgresql",
            "postgresql-0",
            peers_ips,
            "fake-superuser-password",
            "fake-replication-password",
        )

    @patch("charms.operator_libs_linux.v0.apt.DebianPackage.from_system")
    def test_get_postgresql_version(self, _from_system):
        # Mock the package returned by from_system call.
        _from_system.return_value = DebianPackage(
            "postgresql", "12+214ubuntu0.1", "", "all", PackageState.Present
        )
        version = self.patroni._get_postgresql_version()
        _from_system.assert_called_once_with("postgresql")
        self.assertEqual(version, "12")

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
            self.patroni._render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        self.assertEqual(mock.call_args_list[0][0], (filename, "w+"))
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("postgres")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

    @patch("charm.Patroni._render_file")
    @patch("charm.Patroni._create_directory")
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
            self.patroni._render_patroni_service_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.service.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            "/etc/systemd/system/patroni.service",
            expected_content,
            0o644,
        )

    @patch("charm.Patroni._render_file")
    @patch("charm.Patroni._create_directory")
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
            peers_ips=self.peers_ips,
            scope=scope,
            self_ip=self.patroni.unit_ip,
            superuser_password=superuser_password,
            replication_password=replication_password,
            version=self.patroni._get_postgresql_version(),
        )
        print(expected_content)

        # Setup a mock for the `open` method, set returned data to patroni.yml template.
        with open("templates/patroni.yml.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method.
            self.patroni._render_patroni_yml_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.yml.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content,
            0o644,
        )

    @patch("charm.Patroni._render_file")
    @patch("charm.Patroni._create_directory")
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
            self.patroni._render_postgresql_conf_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/postgresql.conf.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/conf.d/postgresql-operator.conf",
            expected_content,
            0o644,
        )

    @patch("cluster.service_start")
    @patch("cluster.service_running")
    @patch("charm.Patroni._create_directory")
    def test_start_patroni(self, _create_directory, _service_running, _service_start):
        _service_running.side_effect = [True, False]

        # Test a success scenario.
        success = self.patroni.start_patroni()
        _service_start.assert_called_with(PATRONI_SERVICE)
        _service_running.assert_called_with(PATRONI_SERVICE)
        assert success

        # Test a fail scenario.
        success = self.patroni.start_patroni()
        assert not success
