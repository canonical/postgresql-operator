# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, mock_open, patch, sentinel

import pytest
import requests
from charms.operator_libs_linux.v2 import snap
from jinja2 import Template
from ops.testing import Harness
from pysyncobj.utility import UtilityException
from tenacity import (
    AttemptManager,
    RetryCallState,
    RetryError,
    Retrying,
    stop_after_delay,
    wait_fixed,
)

from charm import PostgresqlOperatorCharm
from cluster import (
    PATRONI_TIMEOUT,
    Patroni,
    RemoveRaftMemberFailedError,
    SwitchoverFailedError,
    SwitchoverNotSyncError,
)
from constants import (
    PATRONI_CONF_PATH,
    PATRONI_LOGS_PATH,
    POSTGRESQL_DATA_PATH,
    POSTGRESQL_LOGS_PATH,
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
        "http://server1/health": {"state": "running"},
        "http://server4/cluster": {"members": []},
    }
    if args[0] in data:
        return MockResponse(data[args[0]])

    raise requests.exceptions.Timeout()


@pytest.fixture(autouse=True)
def peers_ips():
    peers_ips = {"2.2.2.2", "3.3.3.3"}
    yield peers_ips


@pytest.fixture()
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    yield harness
    harness.cleanup()


@pytest.fixture(autouse=True)
def patroni(harness, peers_ips):
    patroni = Patroni(
        harness.charm,
        "1.1.1.1",
        "postgresql",
        "postgresql-0",
        1,
        peers_ips,
        "fake-superuser-password",
        "fake-replication-password",
        "fake-rewind-password",
        False,
        "fake-raft-password",
        "fake-patroni-password",
    )
    yield patroni


def test_get_alternative_patroni_url(peers_ips, patroni):
    # Mock tenacity attempt.
    retry = Retrying()
    retry_state = RetryCallState(retry, None, None, None)
    attempt = AttemptManager(retry_state)

    # Test the first URL that is returned (it should have the current unit IP).
    url = patroni._get_alternative_patroni_url(attempt)
    assert url == f"http://{patroni.unit_ip}:8008"

    # Test returning the other servers URLs.
    for attempt_number in range(attempt.retry_state.attempt_number + 1, len(peers_ips) + 2):
        attempt.retry_state.attempt_number = attempt_number
        url = patroni._get_alternative_patroni_url(attempt)
        assert url.split("http://")[1].split(":8008")[0] in peers_ips


def test_get_member_ip(peers_ips, patroni):
    with (
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._get_alternative_patroni_url") as _get_alternative_patroni_url,
    ):
        # Test error on trying to get the member IP.
        _get_alternative_patroni_url.side_effect = "http://server2"
        with pytest.raises(RetryError):
            patroni.get_member_ip(patroni.member_name)
            assert False

        # Test using an alternative Patroni URL.
        _get_alternative_patroni_url.side_effect = [
            "http://server3",
            "http://server2",
            "http://server1",
        ]
        ip = patroni.get_member_ip(patroni.member_name)
        assert ip == "1.1.1.1"

        # Test using the current Patroni URL.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        ip = patroni.get_member_ip(patroni.member_name)
        assert ip == "1.1.1.1"

        # Test when not having that specific member in the cluster.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        ip = patroni.get_member_ip("other-member-name")
        assert ip is None


def test_get_patroni_health(peers_ips, patroni):
    with (
        patch("cluster.stop_after_delay", new_callable=PropertyMock) as _stop_after_delay,
        patch("cluster.wait_fixed", new_callable=PropertyMock) as _wait_fixed,
        patch("charm.Patroni._patroni_url", new_callable=PropertyMock) as _patroni_url,
        patch("requests.get", side_effect=mocked_requests_get) as _get,
    ):
        # Test when the Patroni API is reachable.
        _patroni_url.return_value = "http://server1"
        health = patroni.get_patroni_health()

        # Check needed to ensure a fast charm deployment.
        _stop_after_delay.assert_called_once_with(60)
        _wait_fixed.assert_called_once_with(7)

        assert health == {"state": "running"}

        # Test when the Patroni API is not reachable.
        _patroni_url.return_value = "http://server2"
        with pytest.raises(RetryError):
            patroni.get_patroni_health()
            assert False


def test_get_postgresql_version(peers_ips, patroni):
    with patch("charm.snap.SnapClient") as _snap_client:
        # TODO test a real implementation
        _get_installed_snaps = _snap_client.return_value.get_installed_snaps
        _get_installed_snaps.return_value = [
            {"name": "something"},
            {"name": "charmed-postgresql", "version": "14.0"},
        ]
        version = patroni.get_postgresql_version()

        assert version == "14.0"
        _snap_client.assert_called_once_with()
        _get_installed_snaps.assert_called_once_with()


def test_dict_to_hba_string(harness, patroni):
    mock_data = {
        "ldapbasedn": "dc=example,dc=net",
        "ldapbinddn": "cn=serviceuser,dc=example,dc=net",
        "ldapbindpasswd": "password",
        "ldaptls": False,
        "ldapurl": "ldap://0.0.0.0:3893",
    }

    assert patroni._dict_to_hba_string(mock_data) == (
        'ldapbasedn="dc=example,dc=net" '
        'ldapbinddn="cn=serviceuser,dc=example,dc=net" '
        'ldapbindpasswd="password" '
        "ldaptls=0 "
        'ldapurl="ldap://0.0.0.0:3893"'
    )


def test_get_primary(peers_ips, patroni):
    with (
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._get_alternative_patroni_url") as _get_alternative_patroni_url,
    ):
        # Test error on trying to get the member IP.
        _get_alternative_patroni_url.side_effect = "http://server2"
        with pytest.raises(RetryError):
            patroni.get_primary(patroni.member_name)
            assert False

        # Test using an alternative Patroni URL.
        _get_alternative_patroni_url.side_effect = [
            "http://server3",
            "http://server2",
            "http://server1",
        ]
        primary = patroni.get_primary()
        assert primary == "postgresql-0"

        # Test using the current Patroni URL.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        primary = patroni.get_primary()
        assert primary == "postgresql-0"

        # Test requesting the primary in the unit name pattern.
        _get_alternative_patroni_url.side_effect = ["http://server1"]
        primary = patroni.get_primary(unit_name_pattern=True)
        assert primary == "postgresql/0"


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
        assert patroni.is_creating_backup

        # Test when no member is creating a backup.
        response.json.return_value = {
            "members": [{"name": "postgresql-0"}, {"name": "postgresql-1"}]
        }
        assert not patroni.is_creating_backup


def test_is_replication_healthy(peers_ips, patroni):
    with (
        patch("requests.get") as _get,
        patch("charm.Patroni.get_primary"),
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
    ):
        # Test when replication is healthy.
        _get.return_value.status_code = 200
        assert patroni.is_replication_healthy()

        # Test when replication is not healthy.
        _get.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=200),
            MagicMock(status_code=503),
        ]
        assert not patroni.is_replication_healthy()

        # Test ignoring errors in case of raft encryption.
        _get.side_effect = None
        _get.return_value.status_code = 503
        assert patroni.is_replication_healthy(True)


def test_is_member_isolated(peers_ips, patroni):
    with (
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
        patch("requests.get", side_effect=mocked_requests_get) as _get,
        patch("charm.Patroni._patroni_url", new_callable=PropertyMock) as _patroni_url,
    ):
        # Test when it wasn't possible to connect to the Patroni API.
        _patroni_url.return_value = "http://server3"
        assert not patroni.is_member_isolated

        # Test when the member isn't isolated from the cluster.
        _patroni_url.return_value = "http://server1"
        assert not patroni.is_member_isolated

        # Test when the member is isolated from the cluster.
        _patroni_url.return_value = "http://server4"
        assert patroni.is_member_isolated


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
        assert mock.call_args_list[0][0] == (filename, "w+")
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("snap_daemon")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

        # Test when it's requested to not change the file owner.
        mock.reset_mock()
        _pwnam.reset_mock()
        _chmod.reset_mock()
        _chown.reset_mock()
        with patch("builtins.open", mock, create=True):
            patroni.render_file(filename, "rendered-content", 0o640, change_owner=False)
        _pwnam.assert_not_called()
        _chmod.assert_called_once_with(filename, 0o640)
        _chown.assert_not_called()


def test_render_patroni_yml_file(peers_ips, patroni):
    with (
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication.get_partner_addresses",
            return_value=["2.2.2.2", "3.3.3.3"],
        ) as _get_partner_addresses,
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
        raft_password = "fake-raft-password"
        patroni_password = "fake-patroni-password"
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
            partner_addrs=["2.2.2.2", "3.3.3.3"],
            peers_ips=sorted(peers_ips),
            scope=scope,
            self_ip=patroni.unit_ip,
            superuser="operator",
            superuser_password=superuser_password,
            replication_password=replication_password,
            rewind_user=REWIND_USER,
            rewind_password=rewind_password,
            version=postgresql_version,
            synchronous_node_count=0,
            raft_password=raft_password,
            patroni_password=patroni_password,
        )

        # Setup a mock for the `open` method, set returned data to patroni.yml template.
        with open("templates/patroni.yml.j2") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method.
            patroni.render_patroni_yml_file()

        # Check the template is opened read-only in the call to open.
        assert mock.call_args_list[0][0] == ("templates/patroni.yml.j2",)
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
        with patch.object(Retrying, "iter", Mock(side_effect=RetryError(None))):
            lag = patroni.member_replication_lag
            assert lag == "unknown"


def test_reinitialize_postgresql(peers_ips, patroni):
    with patch("requests.post") as _post:
        patroni.reinitialize_postgresql()
        _post.assert_called_once_with(
            f"http://{patroni.unit_ip}:8008/reinitialize",
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )


def test_switchover(peers_ips, patroni):
    with (
        patch("requests.post") as _post,
        patch("cluster.Patroni.get_primary", return_value="primary"),
    ):
        response = _post.return_value
        response.status_code = 200

        patroni.switchover()

        _post.assert_called_once_with(
            "http://1.1.1.1:8008/switchover",
            json={"leader": "primary"},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        _post.reset_mock()

        # Test candidate
        patroni.switchover("candidate")

        _post.assert_called_once_with(
            "http://1.1.1.1:8008/switchover",
            json={"leader": "primary", "candidate": "candidate"},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

        # Test candidate, not sync
        response = _post.return_value
        response.status_code = 412
        response.text = "candidate name does not match with sync_standby"
        with pytest.raises(SwitchoverNotSyncError):
            patroni.switchover("candidate")
            assert False

        # Test general error
        response = _post.return_value
        response.status_code = 412
        response.text = "something else "
        with pytest.raises(SwitchoverFailedError):
            patroni.switchover()
            assert False


def test_update_synchronous_node_count(peers_ips, patroni):
    with (
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)) as _wait_fixed,
        patch("cluster.wait_fixed", return_value=wait_fixed(0)) as _wait_fixed,
        patch("requests.patch") as _patch,
    ):
        response = _patch.return_value
        response.status_code = 200

        patroni.update_synchronous_node_count()

        _patch.assert_called_once_with(
            "http://1.1.1.1:8008/config",
            json={"synchronous_node_count": 0, "synchronous_mode_strict": False},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

        # Test when the request fails.
        response.status_code = 500
        with pytest.raises(RetryError):
            patroni.update_synchronous_node_count()
            assert False


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
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.Patroni.is_patroni_running", return_value=True),
    ):
        _get.return_value.json.return_value = {"state": "running"}

        assert patroni.member_started

        _get.assert_called_once_with(
            "http://1.1.1.1:8008/health", verify=True, timeout=5, auth=patroni._patroni_auth
        )


def test_member_started_false(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.Patroni.is_patroni_running", return_value=True),
    ):
        _get.return_value.json.return_value = {"state": "stopped"}

        assert not patroni.member_started

        _get.assert_called_once_with(
            "http://1.1.1.1:8008/health", verify=True, timeout=5, auth=patroni._patroni_auth
        )


def test_member_started_error(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.Patroni.is_patroni_running", return_value=True),
    ):
        _get.side_effect = Exception

        assert not patroni.member_started

        _get.assert_called_once_with(
            "http://1.1.1.1:8008/health", verify=True, timeout=5, auth=patroni._patroni_auth
        )


def test_member_inactive_true(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "stopped"}

        assert patroni.member_inactive

        _get.assert_called_once_with(
            "http://1.1.1.1:8008/health", verify=True, timeout=5, auth=patroni._patroni_auth
        )


def test_member_inactive_false(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "starting"}

        assert not patroni.member_inactive

        _get.assert_called_once_with(
            "http://1.1.1.1:8008/health", verify=True, timeout=5, auth=patroni._patroni_auth
        )


def test_member_inactive_error(peers_ips, patroni):
    with (
        patch("cluster.requests.get") as _get,
        patch("cluster.stop_after_delay", return_value=stop_after_delay(0)),
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
    ):
        _get.side_effect = Exception

        assert patroni.member_inactive

        _get.assert_called_once_with(
            "http://1.1.1.1:8008/health", verify=True, timeout=5, auth=patroni._patroni_auth
        )


def test_patroni_logs(patroni):
    with patch("charm.snap.SnapCache") as _snap_cache:
        # Test when the logs are returned successfully.
        logs = _snap_cache.return_value.__getitem__.return_value.logs
        logs.return_value = "fake-logs"
        assert patroni.patroni_logs() == "fake-logs"

        # Test the charm fails to get the logs.
        logs.side_effect = snap.SnapError
        assert patroni.patroni_logs() == ""


def test_last_postgresql_logs(patroni):
    with (
        patch("glob.glob") as _glob,
        patch("builtins.open", mock_open(read_data="fake-logs")) as _open,
    ):
        # Test when there are no files to read.
        assert patroni.last_postgresql_logs() == ""
        _open.assert_not_called()

        # Test when there are multiple files in the logs directory.
        _glob.return_value = [
            "/var/snap/charmed-postgresql/common/var/log/postgresql/postgresql.log.1",
            "/var/snap/charmed-postgresql/common/var/log/postgresql/postgresql.log.2",
            "/var/snap/charmed-postgresql/common/var/log/postgresql/postgresql.log.3",
        ]
        assert patroni.last_postgresql_logs() == "fake-logs"
        _open.assert_called_once_with(
            "/var/snap/charmed-postgresql/common/var/log/postgresql/postgresql.log.3"
        )

        # Test when the charm fails to read the logs.
        _open.reset_mock()
        _open.side_effect = OSError
        assert patroni.last_postgresql_logs() == ""
        _open.assert_called_with(
            "/var/snap/charmed-postgresql/common/var/log/postgresql/postgresql.log.3"
        )


def test_get_patroni_restart_condition(patroni):
    mock = mock_open()
    with patch("builtins.open", mock) as _open:
        # Test when there is a restart condition set.
        _open.return_value.__enter__.return_value.read.return_value = "Restart=always"
        assert patroni.get_patroni_restart_condition() == "always"

        # Test when there is no restart condition set.
        _open.return_value.__enter__.return_value.read.return_value = ""
        with pytest.raises(RuntimeError):
            patroni.get_patroni_restart_condition()
            assert False


@pytest.mark.parametrize("new_restart_condition", ["on-success", "on-failure"])
def test_update_patroni_restart_condition(patroni, new_restart_condition):
    with (
        patch("builtins.open", mock_open(read_data="Restart=always")) as _open,
        patch("subprocess.run") as _run,
    ):
        _open.return_value.__enter__.return_value.read.return_value = "Restart=always"
        patroni.update_patroni_restart_condition(new_restart_condition)
        _open.return_value.__enter__.return_value.write.assert_called_once_with(
            f"Restart={new_restart_condition}"
        )
        _run.assert_called_once_with(["/bin/systemctl", "daemon-reload"])


def test_remove_raft_member(patroni):
    with patch("cluster.TcpUtility") as _tcp_utility:
        # Member already removed
        _tcp_utility.return_value.executeCommand.return_value = ""

        patroni.remove_raft_member("1.2.3.4")

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        _tcp_utility.return_value.executeCommand.assert_called_once_with(
            "127.0.0.1:2222", ["status"]
        )
        _tcp_utility.reset_mock()

        # Removing member
        _tcp_utility.return_value.executeCommand.side_effect = [
            {"partner_node_status_server_1.2.3.4:2222": 0, "has_quorum": True},
            "SUCCESS",
        ]

        patroni.remove_raft_member("1.2.3.4")

        _tcp_utility.assert_called_once_with(password="fake-raft-password", timeout=3)
        assert _tcp_utility.return_value.executeCommand.call_count == 2
        _tcp_utility.return_value.executeCommand.assert_any_call("127.0.0.1:2222", ["status"])
        _tcp_utility.return_value.executeCommand.assert_any_call(
            "127.0.0.1:2222", ["remove", "1.2.3.4:2222"]
        )
        _tcp_utility.reset_mock()

        # Raises on failed status
        _tcp_utility.return_value.executeCommand.side_effect = [
            {"partner_node_status_server_1.2.3.4:2222": 0, "has_quorum": True},
            "FAIL",
        ]

        with pytest.raises(RemoveRaftMemberFailedError):
            patroni.remove_raft_member("1.2.3.4")
            assert False

        # Raises on remove error
        _tcp_utility.return_value.executeCommand.side_effect = [
            {"partner_node_status_server_1.2.3.4:2222": 0, "has_quorum": True},
            UtilityException,
        ]

        with pytest.raises(RemoveRaftMemberFailedError):
            patroni.remove_raft_member("1.2.3.4")
            assert False

        # Raises on status error
        _tcp_utility.return_value.executeCommand.side_effect = [
            UtilityException,
        ]

        with pytest.raises(RemoveRaftMemberFailedError):
            patroni.remove_raft_member("1.2.3.4")
            assert False


def test_remove_raft_member_no_quorum(patroni, harness):
    with (
        patch("cluster.TcpUtility") as _tcp_utility,
        patch("cluster.requests.get") as _get,
        patch(
            "charm.PostgresqlOperatorCharm.unit_peer_data", new_callable=PropertyMock
        ) as _unit_peer_data,
    ):
        # Async replica
        _unit_peer_data.return_value = {}
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": False,
            "leader": None,
        }
        _get.return_value.json.return_value = {
            "members": [{"role": "async_replica", "name": "postgresql-0"}]
        }

        patroni.remove_raft_member("1.2.3.4")
        assert harness.charm.unit_peer_data == {"raft_stuck": "True"}

        # No health
        _unit_peer_data.return_value = {}
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": False,
            "leader": None,
        }
        _get.side_effect = Exception

        patroni.remove_raft_member("1.2.3.4")

        assert harness.charm.unit_peer_data == {"raft_stuck": "True"}

        # Sync replica
        _unit_peer_data.return_value = {}
        leader_mock = Mock()
        leader_mock.host = "1.2.3.4"
        _tcp_utility.return_value.executeCommand.return_value = {
            "partner_node_status_server_1.2.3.4:2222": 0,
            "has_quorum": False,
            "leader": leader_mock,
        }
        _get.side_effect = None
        _get.return_value.json.return_value = {
            "members": [{"role": "sync_standby", "name": "postgresql-0"}]
        }

        patroni.remove_raft_member("1.2.3.4")

        assert harness.charm.unit_peer_data == {"raft_stuck": "True"}


def test_remove_raft_data(patroni):
    with (
        patch("cluster.Patroni.stop_patroni") as _stop_patroni,
        patch("cluster.psutil") as _psutil,
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
        patch("shutil.rmtree") as _rmtree,
        patch("pathlib.Path.is_dir") as _is_dir,
        patch("pathlib.Path.exists") as _exists,
    ):
        mock_proc_pg = Mock()
        mock_proc_not_pg = Mock()
        mock_proc_pg.name.return_value = "postgres"
        mock_proc_not_pg.name.return_value = "something_else"
        _psutil.process_iter.side_effect = [[mock_proc_not_pg, mock_proc_pg], [mock_proc_not_pg]]

        patroni.remove_raft_data()

        _stop_patroni.assert_called_once_with()
        assert _psutil.process_iter.call_count == 2
        _psutil.process_iter.assert_any_call(["name"])
        _rmtree.assert_called_once_with(Path(f"{PATRONI_CONF_PATH}/raft"))


def test_reinitialise_raft_data(patroni):
    with (
        patch("cluster.Patroni.get_patroni_health") as _get_patroni_health,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("cluster.Patroni.start_patroni") as _start_patroni,
        patch("cluster.Patroni.restart_patroni") as _restart_patroni,
        patch("cluster.psutil") as _psutil,
        patch("cluster.wait_fixed", return_value=wait_fixed(0)),
    ):
        mock_proc_pg = Mock()
        mock_proc_not_pg = Mock()
        mock_proc_pg.name.return_value = "postgres"
        mock_proc_not_pg.name.return_value = "something_else"
        _psutil.process_iter.side_effect = [[mock_proc_not_pg], [mock_proc_not_pg, mock_proc_pg]]
        _get_patroni_health.side_effect = [
            {"role": "replica", "state": "streaming"},
            {"role": "leader", "state": "running"},
        ]

        patroni.reinitialise_raft_data()

        _update_config.assert_called_once_with(no_peers=True)
        _start_patroni.assert_called_once_with()
        _restart_patroni.assert_called_once_with()
        assert _psutil.process_iter.call_count == 2
        _psutil.process_iter.assert_any_call(["name"])


def test_are_replicas_up(patroni):
    with (
        patch("requests.get") as _get,
    ):
        _get.return_value.json.return_value = {
            "members": [
                {"host": "1.1.1.1", "state": "running"},
                {"host": "2.2.2.2", "state": "streaming"},
                {"host": "3.3.3.3", "state": "other state"},
            ]
        }
        assert patroni.are_replicas_up() == {"1.1.1.1": True, "2.2.2.2": True, "3.3.3.3": False}

        # Return None on error
        _get.side_effect = Exception
        assert patroni.are_replicas_up() is None
