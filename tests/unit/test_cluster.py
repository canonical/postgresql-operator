# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest import TestCase
from unittest.mock import MagicMock, Mock, PropertyMock, mock_open, patch, sentinel

import pytest
import requests as requests
import tenacity as tenacity
from charms.operator_libs_linux.v2 import snap
from jinja2 import Template
from tenacity import stop_after_delay

from cluster import Patroni
from constants import (
    PATRONI_CONF_PATH,
    PATRONI_LOGS_PATH,
    POSTGRESQL_DATA_PATH,
    POSTGRESQL_LOGS_PATH,
    REWIND_USER,
)

PATRONI_SERVICE = "patroni"
CREATE_CLUSTER_CONF_PATH = "/var/snap/charmed-postgresql/current/etc/postgresql/postgresql.conf"

# used for assert functions
tc = TestCase()


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


@pytest.fixture(autouse=True)
def peers_ips():
    peers_ips = {"2.2.2.2", "3.3.3.3"}
    yield peers_ips


@pytest.fixture(autouse=True)
def patroni(peers_ips):
    patroni = Patroni(
        "1.1.1.1",
        "postgresql",
        "postgresql-0",
        1,
        peers_ips,
        "fake-superuser-password",
        "fake-replication-password",
        "fake-rewind-password",
        False,
    )
    yield patroni


def test_get_alternative_patroni_url(peers_ips, patroni):
    # Mock tenacity attempt.
    retry = tenacity.Retrying()
    retry_state = tenacity.RetryCallState(retry, None, None, None)
    attempt = tenacity.AttemptManager(retry_state)

    # Test the first URL that is returned (it should have the current unit IP).
    url = patroni._get_alternative_patroni_url(attempt)
    tc.assertEqual(url, f"http://{patroni.unit_ip}:8008")

    # Test returning the other servers URLs.
    for attempt_number in range(attempt.retry_state.attempt_number + 1, len(peers_ips) + 2):
        attempt.retry_state.attempt_number = attempt_number
        url = patroni._get_alternative_patroni_url(attempt)
        tc.assertIn(url.split("http://")[1].split(":8008")[0], peers_ips)


def test_get_member_ip(peers_ips, patroni):
    with (
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._get_alternative_patroni_url") as _get_alternative_patroni_url,
    ):
        # Test error on trying to get the member IP.
        _get_alternative_patroni_url.side_effect = "http://server2"
        with tc.assertRaises(tenacity.RetryError):
            patroni.get_member_ip(patroni.member_name)

        # Test using an alternative Patroni URL.
        _get_alternative_patroni_url.side_effect = [
            "http://server3",
            "http://server2",
            "http://server1",
        ]
        ip = patroni.get_member_ip(patroni.member_name)
        tc.assertEqual(ip, "1.1.1.1")

        # Test using the current Patroni URL.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        ip = patroni.get_member_ip(patroni.member_name)
        tc.assertEqual(ip, "1.1.1.1")

        # Test when not having that specific member in the cluster.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        ip = patroni.get_member_ip("other-member-name")
        tc.assertIsNone(ip)


def test_get_postgresql_version(peers_ips, patroni):
    with patch("charm.snap.SnapClient") as _snap_client:
        # TODO test a real implementation
        _get_installed_snaps = _snap_client.return_value.get_installed_snaps
        _get_installed_snaps.return_value = [
            {"name": "something"},
            {"name": "charmed-postgresql", "version": "14.0"},
        ]
        version = patroni.get_postgresql_version()

        tc.assertEqual(version, "14.0")
        _snap_client.assert_called_once_with()
        _get_installed_snaps.assert_called_once_with()


def test_get_primary(peers_ips, patroni):
    with (
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._get_alternative_patroni_url") as _get_alternative_patroni_url,
    ):
        # Test error on trying to get the member IP.
        _get_alternative_patroni_url.side_effect = "http://server2"
        with tc.assertRaises(tenacity.RetryError):
            patroni.get_primary(patroni.member_name)

        # Test using an alternative Patroni URL.
        _get_alternative_patroni_url.side_effect = [
            "http://server3",
            "http://server2",
            "http://server1",
        ]
        primary = patroni.get_primary()
        tc.assertEqual(primary, "postgresql-0")

        # Test using the current Patroni URL.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        primary = patroni.get_primary()
        tc.assertEqual(primary, "postgresql-0")

        # Test requesting the primary in the unit name pattern.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        primary = patroni.get_primary(unit_name_pattern=True)
        tc.assertEqual(primary, "postgresql/0")


def test_is_creating_backup(peers_ips, patroni):
    with patch("requests.get") as _get:
        # Test when one member is creating a backup.
        response = _get.return_value
        response.json.return_value = {
            "members": [
                {"name": "postgresql-0"},
                {"name": "postgresql-1", "tags": {"is_creating_backup": True}},
            ]
        }
        tc.assertTrue(patroni.is_creating_backup)

        # Test when no member is creating a backup.
        response.json.return_value = {
            "members": [{"name": "postgresql-0"}, {"name": "postgresql-1"}]
        }
        tc.assertFalse(patroni.is_creating_backup)


def test_is_replication_healthy(peers_ips, patroni):
    with (
        patch("requests.get") as _get,
        patch("charm.Patroni.get_primary"),
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
    ):
        # Test when replication is healthy.
        _get.return_value.status_code = 200
        tc.assertTrue(patroni.is_replication_healthy)

        # Test when replication is not healthy.
        _get.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=200),
            MagicMock(status_code=503),
        ]
        tc.assertFalse(patroni.is_replication_healthy)


def test_is_member_isolated(peers_ips, patroni):
    with (
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._patroni_url", new_callable=PropertyMock) as _patroni_url,
    ):
        # Test when it wasn't possible to connect to the Patroni API.
        _patroni_url.return_value = "http://server3"
        tc.assertFalse(patroni.is_member_isolated)

        # Test when the member isn't isolated from the cluster.
        _patroni_url.return_value = "http://server1"
        tc.assertFalse(patroni.is_member_isolated)

        # Test when the member is isolated from the cluster.
        _patroni_url.return_value = "http://server4"
        tc.assertTrue(patroni.is_member_isolated)


def test_render_file(peers_ips, patroni):
    with (
        patch("os.chmod") as _chmod,
        patch("os.chown") as _chown,
        patch("pwd.getpwnam") as _pwnam,
        patch("tempfile.NamedTemporaryFile") as _temp_file,
    ):
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
            patroni.render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        tc.assertEqual(mock.call_args_list[0][0], (filename, "w+"))
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("snap_daemon")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)


def test_render_patroni_yml_file(peers_ips, patroni):
    with (
        patch("charm.Patroni.get_postgresql_version") as _get_postgresql_version,
        patch("charm.Patroni.render_file") as _render_file,
        patch("charm.Patroni._create_directory"),
    ):
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
            postgresql_log_path=POSTGRESQL_LOGS_PATH,
            member_name=member_name,
            peers_ips=peers_ips,
            scope=scope,
            self_ip=patroni.unit_ip,
            superuser="operator",
            superuser_password=superuser_password,
            replication_password=replication_password,
            rewind_user=REWIND_USER,
            rewind_password=rewind_password,
            version=postgresql_version,
            minority_count=patroni.planned_units // 2,
        )

        # Setup a mock for the `open` method, set returned data to patroni.yml template.
        with open("templates/patroni.yml.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method.
            patroni.render_patroni_yml_file()

        # Check the template is opened read-only in the call to open.
        tc.assertEqual(mock.call_args_list[0][0], ("templates/patroni.yml.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            "/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml",
            expected_content,
            0o600,
        )


def test_start_patroni(peers_ips, patroni):
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.Patroni._create_directory") as _create_directory,
    ):
        _cache = _snap_cache.return_value
        _selected_snap = _cache.__getitem__.return_value
        _selected_snap.start.side_effect = [None, snap.SnapError]

        # Test a success scenario.
        assert patroni.start_patroni()
        _cache.__getitem__.assert_called_once_with("charmed-postgresql")
        _selected_snap.start.assert_called_once_with(services=[PATRONI_SERVICE])

        # Test a fail scenario.
        assert not patroni.start_patroni()


def test_stop_patroni(peers_ips, patroni):
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.Patroni._create_directory") as _create_directory,
    ):
        _cache = _snap_cache.return_value
        _selected_snap = _cache.__getitem__.return_value
        _selected_snap.stop.side_effect = [None, snap.SnapError]
        _selected_snap.services.__getitem__.return_value.__getitem__.return_value = False

        # Test a success scenario.
        assert patroni.stop_patroni()
        _cache.__getitem__.assert_called_once_with("charmed-postgresql")
        _selected_snap.stop.assert_called_once_with(services=[PATRONI_SERVICE])
        _selected_snap.services.__getitem__.return_value.__getitem__.assert_called_once_with(
            "active"
        )

        # Test a fail scenario.
        assert not patroni.stop_patroni()


def test_member_replication_lag(peers_ips, patroni):
    with (
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._patroni_url", new_callable=PropertyMock) as _patroni_url,
    ):
        # Test when the cluster member has a value for the lag field.
        _patroni_url.return_value = "http://server1"
        lag = patroni.member_replication_lag
        assert lag == "1"

        # Test when the cluster member doesn't have a value for the lag field.
        patroni.member_name = "postgresql-1"
        lag = patroni.member_replication_lag
        assert lag == "unknown"

        # Test when the API call fails.
        _patroni_url.return_value = "http://server2"
        with patch.object(tenacity.Retrying, "iter", Mock(side_effect=tenacity.RetryError(None))):
            lag = patroni.member_replication_lag
            assert lag == "unknown"


def test_reinitialize_postgresql(peers_ips, patroni):
    with patch("requests.post") as _post:
        patroni.reinitialize_postgresql()
        _post.assert_called_once_with(f"http://{patroni.unit_ip}:8008/reinitialize", verify=True)


def test_switchover(peers_ips, patroni):
    with (
        patch("requests.post") as _post,
        patch("cluster.Patroni.get_primary", return_value="primary"),
    ):
        response = _post.return_value
        response.status_code = 200

        patroni.switchover()

        _post.assert_called_once_with(
            "http://1.1.1.1:8008/switchover", json={"leader": "primary"}, verify=True
        )


def test_update_synchronous_node_count(peers_ips, patroni):
    with patch("requests.patch") as _patch:
        response = _patch.return_value
        response.status_code = 200

        patroni.update_synchronous_node_count()

        _patch.assert_called_once_with(
            "http://1.1.1.1:8008/config", json={"synchronous_node_count": 0}, verify=True
        )


def test_configure_patroni_on_unit(peers_ips, patroni):
    with (
        patch("os.chmod") as _chmod,
        patch("builtins.open") as _open,
        patch("os.chown") as _chown,
        patch("pwd.getpwnam") as _getpwnam,
    ):
        _getpwnam.return_value.pw_uid = sentinel.uid
        _getpwnam.return_value.pw_gid = sentinel.gid

        patroni.configure_patroni_on_unit()

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


def test_member_started_true(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "running"}

        assert patroni.member_started

        _get.assert_called_once_with("http://1.1.1.1:8008/health", verify=True, timeout=5)


def test_member_started_false(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "stopped"}

        assert not patroni.member_started

        _get.assert_called_once_with("http://1.1.1.1:8008/health", verify=True, timeout=5)


def test_member_started_error(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.side_effect = Exception

        assert not patroni.member_started

        _get.assert_called_once_with("http://1.1.1.1:8008/health", verify=True, timeout=5)


def test_member_inactive_true(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "stopped"}

        assert patroni.member_inactive

        _get.assert_called_once_with("http://1.1.1.1:8008/health", verify=True, timeout=5)


def test_member_inactive_false(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "starting"}

        assert not patroni.member_inactive

        _get.assert_called_once_with("http://1.1.1.1:8008/health", verify=True, timeout=5)


def test_member_inactive_error(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.side_effect = Exception

        assert patroni.member_inactive

        _get.assert_called_once_with("http://1.1.1.1:8008/health", verify=True, timeout=5)
