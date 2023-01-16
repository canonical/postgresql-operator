# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest import mock
from unittest.mock import mock_open, patch

import requests as requests
import tenacity as tenacity
from jinja2 import Template

from cluster import Patroni
from lib.charms.operator_libs_linux.v0.apt import DebianPackage, PackageState
from tests.helpers import STORAGE_PATH

PATRONI_SERVICE = "patroni"


# This method will be used by the mock to replace requests.get
def mocked_requests_get(*args, **kwargs):
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data

        def json(self):
            return self.json_data

    data = {
        "http://server1/cluster": {
            "members": [{"name": "postgresql-0", "host": "1.1.1.1", "role": "leader"}]
        },
    }
    if args[0] in data:
        return MockResponse(data[args[0]])

    raise requests.exceptions.Timeout()


class TestCharm(unittest.TestCase):
    def setUp(self):
        # Setup a cluster.
        self.peers_ips = {"2.2.2.2", "3.3.3.3"}

        self.patroni = Patroni(
            "1.1.1.1",
            STORAGE_PATH,
            "postgresql",
            "postgresql-0",
            1,
            self.peers_ips,
            "fake-superuser-password",
            "fake-replication-password",
            False,
        )

    def test_get_alternative_patroni_url(self):
        # Mock tenacity attempt.
        retry = tenacity.Retrying()
        retry_state = tenacity.RetryCallState(retry, None, None, None)
        attempt = tenacity.AttemptManager(retry_state)

        # Test the first URL that is returned (it should have the current unit IP).
        url = self.patroni._get_alternative_patroni_url(attempt)
        self.assertEqual(url, f"http://{self.patroni.unit_ip}:8008")

        # Test returning the other servers URLs.
        for attempt_number in range(
            attempt.retry_state.attempt_number + 1, len(self.peers_ips) + 2
        ):
            attempt.retry_state.attempt_number = attempt_number
            url = self.patroni._get_alternative_patroni_url(attempt)
            self.assertIn(url.split("http://")[1].split(":8008")[0], self.peers_ips)

    @mock.patch("requests.get", side_effect=mocked_requests_get)
    @patch("charm.Patroni._get_alternative_patroni_url")
    def test_get_member_ip(self, _get_alternative_patroni_url, _get):
        # Test error on trying to get the member IP.
        _get_alternative_patroni_url.side_effect = "http://server2"
        with self.assertRaises(tenacity.RetryError):
            self.patroni.get_member_ip(self.patroni.member_name)

        # Test using an alternative Patroni URL.
        _get_alternative_patroni_url.side_effect = [
            "http://server3",
            "http://server2",
            "http://server1",
        ]
        ip = self.patroni.get_member_ip(self.patroni.member_name)
        self.assertEqual(ip, "1.1.1.1")

        # Test using the current Patroni URL.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        ip = self.patroni.get_member_ip(self.patroni.member_name)
        self.assertEqual(ip, "1.1.1.1")

        # Test when not having that specific member in the cluster.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        ip = self.patroni.get_member_ip("other-member-name")
        self.assertIsNone(ip)

    @patch("charms.operator_libs_linux.v0.apt.DebianPackage.from_system")
    def test_get_postgresql_version(self, _from_system):
        # Mock the package returned by from_system call.
        _from_system.return_value = DebianPackage(
            "postgresql", "12+214ubuntu0.1", "", "all", PackageState.Present
        )
        version = self.patroni._get_postgresql_version()
        _from_system.assert_called_once_with("postgresql")
        self.assertEqual(version, "12")

    @mock.patch("requests.get", side_effect=mocked_requests_get)
    @patch("charm.Patroni._get_alternative_patroni_url")
    def test_get_primary(self, _get_alternative_patroni_url, _get):
        # Test error on trying to get the member IP.
        _get_alternative_patroni_url.side_effect = "http://server2"
        with self.assertRaises(tenacity.RetryError):
            self.patroni.get_primary(self.patroni.member_name)

        # Test using an alternative Patroni URL.
        _get_alternative_patroni_url.side_effect = [
            "http://server3",
            "http://server2",
            "http://server1",
        ]
        primary = self.patroni.get_primary()
        self.assertEqual(primary, "postgresql-0")

        # Test using the current Patroni URL.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        primary = self.patroni.get_primary()
        self.assertEqual(primary, "postgresql-0")

        # Test requesting the primary in the unit name pattern.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        primary = self.patroni.get_primary(unit_name_pattern=True)
        self.assertEqual(primary, "postgresql/0")

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
            self.patroni.render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        self.assertEqual(mock.call_args_list[0][0], (filename, "w+"))
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("postgres")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

    @patch("charm.Patroni.render_file")
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

    @patch("charm.Patroni._get_postgresql_version")
    @patch("charm.Patroni.render_file")
    @patch("charm.Patroni._create_directory")
    def test_render_patroni_yml_file(self, _, _render_file, __):
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
            superuser="operator",
            superuser_password=superuser_password,
            replication_password=replication_password,
            version=self.patroni._get_postgresql_version(),
        )

        # Setup a mock for the `open` method, set returned data to patroni.yml template.
        with open("templates/patroni.yml.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method.
            self.patroni.render_patroni_yml_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.yml.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content,
            0o644,
        )

    @patch("charm.Patroni.render_file")
    @patch("charm.Patroni._create_directory")
    def test_render_postgresql_conf_file(self, _, _render_file):
        # Get the expected content from a file.
        with open("templates/postgresql.conf.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            listen_addresses="*",
            logging_collector="on",
            synchronous_commit="off",
            synchronous_standby_names="*",
        )

        # Setup a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/postgresql.conf.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.patroni.render_postgresql_conf_file()

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
    @patch("cluster.service_resume")
    @patch("charm.Patroni._create_directory")
    def test_start_patroni(
        self, _create_directory, _service_resume, _service_running, _service_start
    ):
        _service_running.side_effect = [True, False]

        # Test a success scenario.
        success = self.patroni.start_patroni()
        _service_start.assert_called_with(PATRONI_SERVICE)
        _service_resume.assert_called_with(PATRONI_SERVICE)
        _service_running.assert_called_with(PATRONI_SERVICE)
        assert success

        # Test a fail scenario.
        success = self.patroni.start_patroni()
        assert not success
