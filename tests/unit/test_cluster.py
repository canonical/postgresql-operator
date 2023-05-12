# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, PropertyMock, mock_open, patch, sentinel

import requests as requests
import tenacity as tenacity
from charms.operator_libs_linux.v1 import snap
from jinja2 import Template

from cluster import Patroni
from constants import (
    PATRONI_CONF_PATH,
    PATRONI_LOGS_PATH,
    POSTGRESQL_DATA_PATH,
    REWIND_USER,
)

PATRONI_SERVICE = "patroni"
CREATE_CLUSTER_CONF_PATH = "/var/snap/charmed-postgresql/current/etc/postgresql/postgresql.conf"


# This method will be used by the mock to replace requests.get
def mocked_requests_get(*args, **kwargs):
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data

        def json(self):
            return self.json_data

    data = {
        "http://server1/cluster": {
            "members": [{"name": "postgresql-0", "host": "1.1.1.1", "role": "leader", "lag": "1"}]
        },
        "http://server4/cluster": {"members": []},
    }
    if args[0] in data:
        return MockResponse(data[args[0]])

    raise requests.exceptions.Timeout()


class TestCluster(unittest.TestCase):
    def setUp(self):
        # Setup a cluster.
        self.peers_ips = {"2.2.2.2", "3.3.3.3"}

        self.patroni = Patroni(
            "1.1.1.1",
            "postgresql",
            "postgresql-0",
            1,
            self.peers_ips,
            "fake-superuser-password",
            "fake-replication-password",
            "fake-rewind-password",
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

    @patch("requests.get", side_effect=mocked_requests_get)
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

    @patch("charm.snap.SnapClient")
    def test_get_postgresql_version(self, _snap_client):
        # TODO test a real implementation
        _get_installed_snaps = _snap_client.return_value.get_installed_snaps
        _get_installed_snaps.return_value = [
            {"name": "something"},
            {"name": "charmed-postgresql", "version": "14.0"},
        ]
        version = self.patroni.get_postgresql_version()

        self.assertEqual(version, "14.0")
        _snap_client.assert_called_once_with()
        _get_installed_snaps.assert_called_once_with()

    @patch("requests.get", side_effect=mocked_requests_get)
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

    @patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0))
    @patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0))
    @patch("requests.get", side_effect=mocked_requests_get)
    @patch("charm.Patroni._patroni_url", new_callable=PropertyMock)
    def test_is_member_isolated(self, _patroni_url, _get, _, __):
        # Test when it wasn't possible to connect to the Patroni API.
        _patroni_url.return_value = "http://server3"
        self.assertFalse(self.patroni.is_member_isolated)

        # Test when the member isn't isolated from the cluster.
        _patroni_url.return_value = "http://server1"
        self.assertFalse(self.patroni.is_member_isolated)

        # Test when the member is isolated from the cluster.
        _patroni_url.return_value = "http://server4"
        self.assertTrue(self.patroni.is_member_isolated)

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
        _pwnam.assert_called_with("snap_daemon")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

    @patch("charm.Patroni.get_postgresql_version")
    @patch("charm.Patroni.render_file")
    @patch("charm.Patroni._create_directory")
    def test_render_patroni_yml_file(self, _, _render_file, _get_postgresql_version):
        _get_postgresql_version.return_value = "14.7"

        # Define variables to render in the template.
        member_name = "postgresql-0"
        scope = "postgresql"
        superuser_password = "fake-superuser-password"
        replication_password = "fake-replication-password"
        rewind_password = "fake-rewind-password"
        postgresql_version = "14"

        # Get the expected content from a file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            conf_path=PATRONI_CONF_PATH,
            data_path=POSTGRESQL_DATA_PATH,
            log_path=PATRONI_LOGS_PATH,
            member_name=member_name,
            peers_ips=self.peers_ips,
            scope=scope,
            self_ip=self.patroni.unit_ip,
            superuser="operator",
            superuser_password=superuser_password,
            replication_password=replication_password,
            rewind_user=REWIND_USER,
            rewind_password=rewind_password,
            version=postgresql_version,
            minority_count=self.patroni.planned_units // 2,
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
            "/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml",
            expected_content,
            0o644,
        )

    @patch("charm.snap.SnapCache")
    @patch("charm.Patroni._create_directory")
    def test_start_patroni(self, _create_directory, _snap_cache):
        _cache = _snap_cache.return_value
        _selected_snap = _cache.__getitem__.return_value
        _selected_snap.start.side_effect = [None, snap.SnapError]

        # Test a success scenario.
        assert self.patroni.start_patroni()
        _cache.__getitem__.assert_called_once_with("charmed-postgresql")
        _selected_snap.start.assert_called_once_with(services=[PATRONI_SERVICE])

        # Test a fail scenario.
        assert not self.patroni.start_patroni()

    @patch("charm.snap.SnapCache")
    @patch("charm.Patroni._create_directory")
    def test_stop_patroni(self, _create_directory, _snap_cache):
        _cache = _snap_cache.return_value
        _selected_snap = _cache.__getitem__.return_value
        _selected_snap.stop.side_effect = [None, snap.SnapError]
        _selected_snap.services.__getitem__.return_value.__getitem__.return_value = False

        # Test a success scenario.
        assert self.patroni.stop_patroni()
        _cache.__getitem__.assert_called_once_with("charmed-postgresql")
        _selected_snap.stop.assert_called_once_with(services=[PATRONI_SERVICE])
        _selected_snap.services.__getitem__.return_value.__getitem__.assert_called_once_with(
            "active"
        )

        # Test a fail scenario.
        assert not self.patroni.stop_patroni()

    @patch("requests.get", side_effect=mocked_requests_get)
    @patch("charm.Patroni._patroni_url", new_callable=PropertyMock)
    def test_member_replication_lag(self, _patroni_url, _get):
        # Test when the cluster member has a value for the lag field.
        _patroni_url.return_value = "http://server1"
        lag = self.patroni.member_replication_lag
        assert lag == "1"

        # Test when the cluster member doesn't have a value for the lag field.
        self.patroni.member_name = "postgresql-1"
        lag = self.patroni.member_replication_lag
        assert lag == "unknown"

        # Test when the API call fails.
        _patroni_url.return_value = "http://server2"
        with patch.object(tenacity.Retrying, "iter", Mock(side_effect=tenacity.RetryError(None))):
            lag = self.patroni.member_replication_lag
            assert lag == "unknown"

    @patch("requests.post")
    def test_reinitialize_postgresql(self, _post):
        self.patroni.reinitialize_postgresql()
        _post.assert_called_once_with(
            f"http://{self.patroni.unit_ip}:8008/reinitialize", verify=True
        )

    @patch("requests.post")
    @patch("cluster.Patroni.get_primary", return_value="primary")
    def test_switchover(self, _, _post):
        response = _post.return_value
        response.status_code = 200

        self.patroni.switchover()

        _post.assert_called_once_with(
            "http://1.1.1.1:8008/switchover", json={"leader": "primary"}, verify=True
        )

    @patch("requests.patch")
    def test_update_synchronous_node_count(self, _patch):
        response = _patch.return_value
        response.status_code = 200

        self.patroni.update_synchronous_node_count()

        _patch.assert_called_once_with(
            "http://1.1.1.1:8008/config", json={"synchronous_node_count": 0}, verify=True
        )

    @patch("cluster.Patroni._create_user_home_directory")
    @patch("os.chmod")
    @patch("builtins.open")
    @patch("os.chown")
    @patch("pwd.getpwnam")
    def test_configure_patroni_on_unit(
        self,
        _getpwnam,
        _chown,
        _open,
        _chmod,
        _create_user_home_directory,
    ):
        _getpwnam.return_value.pw_uid = sentinel.uid
        _getpwnam.return_value.pw_gid = sentinel.gid

        self.patroni.configure_patroni_on_unit()

        _getpwnam.assert_called_once_with("snap_daemon")

        _chown.assert_any_call(
            "/var/snap/charmed-postgresql/common/var/lib/postgresql",
            uid=sentinel.uid,
            gid=sentinel.gid,
        )

        _open.assert_called_once_with(CREATE_CLUSTER_CONF_PATH, "a")
        _chmod.assert_called_once_with(
            "/var/snap/charmed-postgresql/common/var/lib/postgresql", 488
        )

        _create_user_home_directory.assert_called_once_with()
