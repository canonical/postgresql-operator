# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, PropertyMock, patch

from charms.operator_libs_linux.v1 import snap
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateUserError,
    PostgreSQLUpdateUserPasswordError,
)
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness
from tenacity import RetryError, stop_after_delay, wait_fixed

from charm import NO_PRIMARY_MESSAGE, PostgresqlOperatorCharm
from cluster import RemoveRaftMemberFailedError
from constants import PEER
from tests.helpers import patch_network_get

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"


class TestCharm(unittest.TestCase):
    def setUp(self):
        self._peer_relation = PEER
        self._postgresql_container = "postgresql"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm
        self.rel_id = self.harness.add_relation(self._peer_relation, self.charm.app.name)

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._patch_snap_seccomp_profile")
    @patch("charm.PostgresqlOperatorCharm._install_snap_packages")
    @patch("charm.PostgresqlOperatorCharm._reboot_on_detached_storage")
    @patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        side_effect=[False, True, True],
    )
    def test_on_install(
        self,
        _is_storage_attached,
        _reboot_on_detached_storage,
        _install_snap_packages,
        _patch_snap_seccomp_profile,
    ):
        # Test without storage.
        self.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()

        # Test without adding Patroni resource.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once()

        # Assert the status set by the event handler.
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._patch_snap_seccomp_profile")
    @patch("charm.PostgresqlOperatorCharm._install_snap_packages")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_install_patch_failure(
        self,
        _is_storage_attached,
        _install_snap_packages,
        _patch_snap_seccomp_profile,
    ):
        # Mock the result of the call.
        _patch_snap_seccomp_profile.side_effect = subprocess.CalledProcessError(1, "fake command")
        # Trigger the hook.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once()
        _patch_snap_seccomp_profile.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._install_snap_packages")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_install_snap_failure(
        self,
        _is_storage_attached,
        _install_snap_packages,
    ):
        # Mock the result of the call.
        _install_snap_packages.side_effect = snap.SnapError
        # Trigger the hook.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock)
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch_network_get(private_address="1.1.1.1")
    def test_on_leader_elected(
        self, _update_config, _primary_endpoint, _update_relation_endpoints
    ):
        # Assert that there is no password in the peer relation.
        self.assertIsNone(self.charm._peers.data[self.charm.app].get("operator-password", None))

        # Check that a new password was generated on leader election.
        _primary_endpoint.return_value = "1.1.1.1"
        self.harness.set_leader()
        password = self.charm._peers.data[self.charm.app].get("operator-password", None)
        _update_config.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        self.assertIsNotNone(password)

        # Mark the cluster as initialised.
        self.charm._peers.data[self.charm.app].update({"cluster_initialised": "True"})

        # Trigger a new leader election and check that the password is still the same
        # and also that update_endpoints was called after the cluster was initialised.
        self.harness.set_leader(False)
        self.harness.set_leader()
        self.assertEqual(
            self.charm._peers.data[self.charm.app].get("operator-password", None), password
        )
        _update_relation_endpoints.assert_called_once()
        self.assertFalse(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Check for a BlockedStatus when there is no primary endpoint.
        _primary_endpoint.return_value = None
        self.harness.set_leader(False)
        self.harness.set_leader()
        _update_relation_endpoints.assert_called_once()  # Assert it was not called again.
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch("charm.Patroni.get_postgresql_version")
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgreSQLProvider.oversee_users")
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch("charm.PostgreSQLProvider.update_endpoints")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch(
        "charm.Patroni.member_started",
        new_callable=PropertyMock,
    )
    @patch("charm.Patroni.bootstrap_cluster")
    @patch("charm.PostgresqlOperatorCharm._replication_password")
    @patch("charm.PostgresqlOperatorCharm._get_password")
    @patch("charm.PostgresqlOperatorCharm._reboot_on_detached_storage")
    @patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        side_effect=[False, True, True, True, True],
    )
    def test_on_start(
        self,
        _is_storage_attached,
        _reboot_on_detached_storage,
        _get_password,
        _replication_password,
        _bootstrap_cluster,
        _member_started,
        _,
        __,
        _postgresql,
        _update_relation_endpoints,
        _oversee_users,
        _get_postgresql_version,
    ):
        _get_postgresql_version.return_value = "14.0"

        # Test without storage.
        self.charm.on.start.emit()
        _reboot_on_detached_storage.assert_called_once()

        # Test before the passwords are generated.
        _member_started.return_value = False
        _get_password.return_value = None
        self.charm.on.start.emit()
        _bootstrap_cluster.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

        # Mock the passwords.
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"

        # Mock cluster start and postgres user creation success values.
        _bootstrap_cluster.side_effect = [False, True, True]
        _postgresql.list_users.side_effect = [[], [], []]
        _postgresql.create_user.side_effect = [PostgreSQLCreateUserError, None, None]

        # Test for a failed cluster bootstrapping.
        # TODO: test replicas start (DPE-494).
        self.harness.set_leader()
        self.charm.on.start.emit()
        _bootstrap_cluster.assert_called_once()
        _oversee_users.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Set an initial waiting status (like after the install hook was triggered).
        self.harness.model.unit.status = WaitingStatus("fake message")

        # Test the event of an error happening when trying to create the default postgres user.
        _member_started.return_value = True
        self.charm.on.start.emit()
        _postgresql.create_user.assert_called_once()
        _oversee_users.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Set an initial waiting status again (like after the install hook was triggered).
        self.harness.model.unit.status = WaitingStatus("fake message")

        # Then test the event of a correct cluster bootstrapping.
        self.charm.on.start.emit()
        self.assertEqual(
            _postgresql.create_user.call_count, 3
        )  # Considering the previous failed call.
        _oversee_users.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

    @patch("charm.Patroni.get_postgresql_version")
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.configure_patroni_on_unit")
    @patch(
        "charm.Patroni.member_started",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock)
    @patch.object(EventBase, "defer")
    @patch("charm.PostgresqlOperatorCharm._replication_password")
    @patch("charm.PostgresqlOperatorCharm._get_password")
    @patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        return_value=True,
    )
    def test_on_start_replica(
        self,
        _is_storage_attached,
        _get_password,
        _replication_password,
        _defer,
        _update_relation_endpoints,
        _member_started,
        _configure_patroni_on_unit,
        _get_postgresql_version,
    ):
        _get_postgresql_version.return_value = "14.0"

        # Set the current unit to be a replica (non leader unit).
        self.harness.set_leader(False)

        # Mock the passwords.
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"

        # Test an uninitialized cluster.
        self.charm._peers.data[self.charm.app].update({"cluster_initialised": ""})
        self.charm.on.start.emit()
        _defer.assert_called_once()

        # Set an initial waiting status again (like after a machine restart).
        self.harness.model.unit.status = WaitingStatus("fake message")

        # Mark the cluster as initialised and with the workload up and running.
        self.charm._peers.data[self.charm.app].update({"cluster_initialised": "True"})
        _member_started.return_value = True
        self.charm.on.start.emit()
        _configure_patroni_on_unit.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

        # Set an initial waiting status (like after the install hook was triggered).
        self.harness.model.unit.status = WaitingStatus("fake message")

        # Check that the unit status doesn't change when the workload is not running.
        # In that situation only Patroni is configured in the unit (but not started).
        _member_started.return_value = False
        self.charm.on.start.emit()
        _configure_patroni_on_unit.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch("charm.Patroni")
    @patch("charm.PostgresqlOperatorCharm._get_password")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_start_no_patroni_member(
        self,
        _is_storage_attached,
        _get_password,
        patroni,
        _postgresql,
    ):
        # Mock the passwords.
        patroni.return_value.member_started = False
        _get_password.return_value = "fake-operator-password"
        bootstrap_cluster = patroni.return_value.bootstrap_cluster
        bootstrap_cluster.return_value = True

        patroni.return_value.get_postgresql_version.return_value = "14.0"

        self.harness.set_leader()
        self.charm.on.start.emit()
        bootstrap_cluster.assert_called_once()
        _postgresql.create_user.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))
        self.assertEqual(self.harness.model.unit.status.message, "awaiting for member to start")

    @patch("charm.Patroni.bootstrap_cluster")
    @patch("charm.PostgresqlOperatorCharm._replication_password")
    @patch("charm.PostgresqlOperatorCharm._get_password")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_start_after_blocked_state(
        self, _is_storage_attached, _get_password, _replication_password, _bootstrap_cluster
    ):
        # Set an initial blocked status (like after the install hook was triggered).
        initial_status = BlockedStatus("fake message")
        self.harness.model.unit.status = initial_status

        # Test for a failed cluster bootstrapping.
        self.charm.on.start.emit()
        _get_password.assert_not_called()
        _replication_password.assert_not_called()
        _bootstrap_cluster.assert_not_called()
        # Assert the status didn't change.
        self.assertEqual(self.harness.model.unit.status, initial_status)

    def test_on_get_password(self):
        # Create a mock event and set passwords in peer relation data.
        mock_event = MagicMock(params={})
        self.harness.update_relation_data(
            self.rel_id,
            self.charm.app.name,
            {
                "operator-password": "test-password",
                "replication-password": "replication-test-password",
            },
        )

        # Test providing an invalid username.
        mock_event.params["username"] = "user"
        self.charm._on_get_password(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test without providing the username option.
        mock_event.reset_mock()
        del mock_event.params["username"]
        self.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "test-password"})

        # Also test providing the username option.
        mock_event.reset_mock()
        mock_event.params["username"] = "replication"
        self.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "replication-test-password"})

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgresqlOperatorCharm.set_secret")
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch("charm.Patroni.are_all_members_ready")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_set_password(
        self,
        _,
        _are_all_members_ready,
        _postgresql,
        _set_secret,
        __,
    ):
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Set some values for the other mocks.
        _are_all_members_ready.side_effect = [False, True, True, True, True]
        _postgresql.update_user_password = PropertyMock(
            side_effect=[PostgreSQLUpdateUserPasswordError, None, None, None]
        )

        # Test trying to set a password through a non leader unit.
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test providing an invalid username.
        self.harness.set_leader()
        mock_event.reset_mock()
        mock_event.params["username"] = "user"
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option but without all cluster members ready.
        mock_event.reset_mock()
        del mock_event.params["username"]
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test for an error updating when updating the user password in the database.
        mock_event.reset_mock()
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option.
        self.charm._on_set_password(mock_event)
        self.assertEqual(_set_secret.call_args_list[0][0][1], "operator-password")

        # Also test providing the username option.
        _set_secret.reset_mock()
        mock_event.params["username"] = "replication"
        self.charm._on_set_password(mock_event)
        self.assertEqual(_set_secret.call_args_list[0][0][1], "replication-password")

        # And test providing both the username and password options.
        _set_secret.reset_mock()
        mock_event.params["password"] = "replication-test-password"
        self.charm._on_set_password(mock_event)
        _set_secret.assert_called_once_with(
            "app", "replication-password", "replication-test-password"
        )

    @patch("charm.wait_fixed", return_vaule=wait_fixed(0))
    @patch("charm.stop_after_delay", return_value=stop_after_delay(0))
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._set_primary_status_message")
    @patch("charm.Patroni.restart_patroni")
    @patch("charm.Patroni.is_member_isolated")
    @patch("charm.Patroni.reinitialize_postgresql")
    @patch("charm.Patroni.member_replication_lag", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch("charm.PostgreSQLProvider.oversee_users")
    def test_on_update_status(
        self,
        _oversee_users,
        _update_relation_endpoints,
        _member_started,
        _member_replication_lag,
        _reinitialize_postgresql,
        _is_member_isolated,
        _restart_patroni,
        _set_primary_status_message,
        _,
        __,
    ):
        # Test before the cluster is initialised.
        self.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()

        # Test after the cluster was initialised, but with the unit in a blocked state.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.app.name, {"cluster_initialised": "True"}
            )
        self.charm.unit.status = BlockedStatus("fake blocked status")
        self.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()

        # Test with the unit in a status different that blocked.
        self.charm.unit.status = ActiveStatus()
        self.charm.on.update_status.emit()
        _set_primary_status_message.assert_called_once()

        # Test the reinitialisation of the replica when its lag is unknown
        # after a restart.
        _set_primary_status_message.reset_mock()
        _member_started.return_value = False
        _is_member_isolated.return_value = False
        _member_replication_lag.return_value = "unknown"
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"postgresql_restarted": "True"}
            )
        self.charm.on.update_status.emit()
        _reinitialize_postgresql.assert_called_once()
        _restart_patroni.assert_not_called()
        _set_primary_status_message.assert_not_called()

        # Test call to restart when the member is isolated from the cluster.
        _is_member_isolated.return_value = True
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"postgresql_restarted": ""}
            )
        self.charm.on.update_status.emit()
        _restart_patroni.assert_called_once()

    @patch("charm.snap.SnapCache")
    def test_install_snap_packages(self, _snap_cache):
        _snap_package = _snap_cache.return_value.__getitem__.return_value
        _snap_package.ensure.side_effect = snap.SnapError
        _snap_package.present = False

        # Test for problem with snap update.
        with self.assertRaises(snap.SnapError):
            self.charm._install_snap_packages([("postgresql", {"channel": "14/edge"})])
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_cache.assert_called_once_with()
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, channel="14/edge")

        # Test with a not found package.
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = snap.SnapNotFoundError
        with self.assertRaises(snap.SnapNotFoundError):
            self.charm._install_snap_packages([("postgresql", {"channel": "14/edge"})])
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_cache.assert_called_once_with()
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, channel="14/edge")

        # Then test a valid one.
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = None
        self.charm._install_snap_packages([("postgresql", {"channel": "14/edge"})])
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, channel="14/edge")
        _snap_package.hold.assert_not_called()

        # Test revision
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = None
        self.charm._install_snap_packages([("postgresql", {"revision": 42})])
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, revision=42)
        _snap_package.hold.assert_called_once_with()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_get_secret(self, _):
        self.harness.set_leader()

        # Test application scope.
        assert self.charm.get_secret("app", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {"password": "test-password"}
        )
        assert self.charm.get_secret("app", "password") == "test-password"

        # Test unit scope.
        assert self.charm.get_secret("unit", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.unit.name, {"password": "test-password"}
        )
        assert self.charm.get_secret("unit", "password") == "test-password"

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_set_secret(self, _):
        self.harness.set_leader()

        # Test application scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.app.name)
        self.charm.set_secret("app", "password", "test-password")
        assert (
            self.harness.get_relation_data(self.rel_id, self.charm.app.name)["password"]
            == "test-password"
        )

        # Test unit scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
        self.charm.set_secret("unit", "password", "test-password")
        assert (
            self.harness.get_relation_data(self.rel_id, self.charm.unit.name)["password"]
            == "test-password"
        )

    @patch(
        "subprocess.check_call",
        side_effect=[None, subprocess.CalledProcessError(1, "fake command")],
    )
    def test_is_storage_attached(self, _check_call):
        # Test with attached storage.
        is_storage_attached = self.charm._is_storage_attached()
        _check_call.assert_called_once_with(["mountpoint", "-q", self.charm._storage_path])
        self.assertTrue(is_storage_attached)

        # Test with detached storage.
        is_storage_attached = self.charm._is_storage_attached()
        self.assertFalse(is_storage_attached)

    @patch("subprocess.check_call")
    def test_reboot_on_detached_storage(self, _check_call):
        mock_event = MagicMock()
        self.charm._reboot_on_detached_storage(mock_event)
        mock_event.defer.assert_called_once()
        self.assertTrue(isinstance(self.charm.unit.status, WaitingStatus))
        _check_call.assert_called_once_with(["systemctl", "reboot"])

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.restart_postgresql")
    def test_restart(self, _restart_postgresql):
        # Test a successful restart.
        self.charm._restart(None)
        self.assertFalse(isinstance(self.charm.unit.status, BlockedStatus))

        # Test a failed restart.
        _restart_postgresql.side_effect = RetryError(last_attempt=1)
        self.charm._restart(None)
        self.assertTrue(isinstance(self.charm.unit.status, BlockedStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock")
    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.Patroni.render_patroni_yml_file")
    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS.get_tls_files")
    def test_update_config(
        self,
        _get_tls_files,
        _render_patroni_yml_file,
        _member_started,
        _reload_patroni_configuration,
        _restart,
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Mock some properties.
            postgresql_mock.is_tls_enabled = PropertyMock(side_effect=[False, False, False])
            _member_started.side_effect = [True, True, False]

            # Test without TLS files available.
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"tls": "enabled"}
            )  # Mock some data in the relation to test that it change.
            _get_tls_files.return_value = [None]
            self.charm.update_config()
            _render_patroni_yml_file.assert_called_once_with(
                archive_mode="on", enable_tls=False, backup_id=None, stanza=None
            )
            _reload_patroni_configuration.assert_called_once()
            _restart.assert_not_called()
            self.assertNotIn(
                "tls", self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
            )

            # Test with TLS files available.
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"tls": ""}
            )  # Mock some data in the relation to test that it change.
            _get_tls_files.return_value = ["something"]
            _render_patroni_yml_file.reset_mock()
            _reload_patroni_configuration.reset_mock()
            self.charm.update_config()
            _render_patroni_yml_file.assert_called_once_with(
                archive_mode="on", enable_tls=True, backup_id=None, stanza=None
            )
            _reload_patroni_configuration.assert_called_once()
            _restart.assert_called_once()
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.charm.unit.name)["tls"], "enabled"
            )

            # Test with member not started yet.
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"tls": ""}
            )  # Mock some data in the relation to test that it change.
            _reload_patroni_configuration.reset_mock()
            self.charm.update_config()
            _reload_patroni_configuration.assert_not_called()
            _restart.assert_called_once()
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.charm.unit.name)["tls"], "enabled"
            )

    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch("charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock)
    def test_on_cluster_topology_change(self, _primary_endpoint, _update_relation_endpoints):
        # Mock the property value.
        _primary_endpoint.side_effect = [None, "1.1.1.1"]

        # Test without an elected primary.
        self.charm._on_cluster_topology_change(Mock())
        _update_relation_endpoints.assert_not_called()

        # Test with an elected primary.
        self.charm._on_cluster_topology_change(Mock())
        _update_relation_endpoints.assert_called_once()

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
        return_value=None,
    )
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    def test_on_cluster_topology_change_keep_blocked(
        self, _update_relation_endpoints, _primary_endpoint
    ):
        self.harness.model.unit.status = BlockedStatus(NO_PRIMARY_MESSAGE)

        self.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_not_called()
        self.assertEqual(_primary_endpoint.call_count, 2)
        _primary_endpoint.assert_called_with()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))
        self.assertEqual(self.harness.model.unit.status.message, NO_PRIMARY_MESSAGE)

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
        return_value="fake-unit",
    )
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    def test_on_cluster_topology_change_clear_blocked(
        self, _update_relation_endpoints, _primary_endpoint
    ):
        self.harness.model.unit.status = BlockedStatus(NO_PRIMARY_MESSAGE)

        self.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_called_once_with()
        self.assertEqual(_primary_endpoint.call_count, 2)
        _primary_endpoint.assert_called_with()
        self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._add_members")
    @patch("charm.PostgresqlOperatorCharm._remove_from_members_ips")
    @patch("charm.Patroni.remove_raft_member")
    def test_reconfigure_cluster(
        self, _remove_raft_member, _remove_from_members_ips, _add_members
    ):
        # Test when no change is needed in the member IP.
        mock_event = Mock()
        mock_event.unit = self.charm.unit
        mock_event.relation.data = {mock_event.unit: {}}
        self.assertTrue(self.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_called_once_with(mock_event)

        # Test when a change is needed in the member IP, but it fails.
        _remove_raft_member.side_effect = RemoveRaftMemberFailedError
        _add_members.reset_mock()
        mock_event.relation.data = {mock_event.unit: {"ip-to-remove": "1.1.1.1"}}
        self.assertFalse(self.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_called_once()
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_not_called()

        # Test when a change is needed in the member IP and it succeeds.
        _remove_raft_member.reset_mock()
        _remove_raft_member.side_effect = None
        _add_members.reset_mock()
        mock_event.relation.data = {mock_event.unit: {"ip-to-remove": "1.1.1.1"}}
        self.assertTrue(self.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_called_once()
        _remove_from_members_ips.assert_called_once()
        _add_members.assert_called_once_with(mock_event)

    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    def test_update_certificate(self, _request_certificate):
        # If there is no current TLS files, _request_certificate should be called
        # only when the certificates relation is established.
        self.charm._update_certificate()
        _request_certificate.assert_not_called()

        # Test with already present TLS files (when they will be replaced by new ones).
        ca = "fake CA"
        cert = "fake certificate"
        key = private_key = "fake private key"
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.unit.name,
                {
                    "ca": ca,
                    "cert": cert,
                    "key": key,
                    "private-key": private_key,
                },
            )
        self.charm._update_certificate()
        _request_certificate.assert_called_once_with(private_key)

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._update_certificate")
    @patch("charm.Patroni.stop_patroni")
    def test_update_member_ip(self, _stop_patroni, _update_certificate):
        # Test when the IP address of the unit hasn't changed.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.unit.name,
                {
                    "ip": "1.1.1.1",
                },
            )
        self.assertFalse(self.charm._update_member_ip())
        relation_data = self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
        self.assertEqual(relation_data.get("ip-to-remove"), None)
        _stop_patroni.assert_not_called()
        _update_certificate.assert_not_called()

        # Test when the IP address of the unit has changed.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.unit.name,
                {
                    "ip": "2.2.2.2",
                },
            )
        self.assertTrue(self.charm._update_member_ip())
        relation_data = self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
        self.assertEqual(relation_data.get("ip"), "1.1.1.1")
        self.assertEqual(relation_data.get("ip-to-remove"), "2.2.2.2")
        _stop_patroni.assert_called_once()
        _update_certificate.assert_called_once()
