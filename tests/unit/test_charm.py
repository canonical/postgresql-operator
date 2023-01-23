# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import os
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, PropertyMock, call, mock_open, patch

import ops.testing
from charms.operator_libs_linux.v0 import apt
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateUserError,
    PostgreSQLUpdateUserPasswordError,
)
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness
from tenacity import RetryError

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"


class TestCharm(unittest.TestCase):
    def setUp(self):
        self._peer_relation = PEER
        self._postgresql_container = "postgresql"
        self._postgresql_service = "postgresql"

        ops.testing.SIMULATE_CAN_CONNECT = True
        self.addCleanup(setattr, ops.testing, "SIMULATE_CAN_CONNECT", False)

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm
        self.rel_id = self.harness.add_relation(self._peer_relation, self.charm.app.name)

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._install_pip_packages")
    @patch("charm.PostgresqlOperatorCharm._install_apt_packages")
    @patch("charm.PostgresqlOperatorCharm._inhibit_default_cluster_creation")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_install(
        self,
        _is_storage_attached,
        _inhibit_default_cluster_creation,
        _install_apt_packages,
        _install_pip_packages,
    ):
        # Test without adding Patroni resource.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _inhibit_default_cluster_creation.assert_called_once()
        _install_apt_packages.assert_called_once()
        # Assert that the needed calls were made.
        _install_pip_packages.assert_not_called()
        # Assert the status set by the event handler.
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Add an empty file as Patroni resource just to check that the correct calls were made.
        self.harness.add_resource("patroni", "")
        self.charm.on.install.emit()
        _install_pip_packages.assert_called_once()
        # Assert the status set by the event handler.
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._install_pip_packages")
    @patch("charm.PostgresqlOperatorCharm._install_apt_packages")
    @patch("charm.PostgresqlOperatorCharm._inhibit_default_cluster_creation")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_install_apt_failure(
        self,
        _is_storage_attached,
        _inhibit_default_cluster_creation,
        _install_apt_packages,
        _install_pip_packages,
    ):
        # Mock the result of the call.
        _install_apt_packages.side_effect = apt.PackageNotFoundError
        # Trigger the hook.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _inhibit_default_cluster_creation.assert_called_once()
        _install_apt_packages.assert_called_once()
        _install_pip_packages.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._install_pip_packages")
    @patch("charm.PostgresqlOperatorCharm._install_apt_packages")
    @patch("charm.PostgresqlOperatorCharm._inhibit_default_cluster_creation")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_install_pip_failure(
        self,
        _is_storage_attached,
        _inhibit_default_cluster_creation,
        _install_apt_packages,
        _install_pip_packages,
    ):
        # Mock the result of the call.
        _install_pip_packages.side_effect = subprocess.CalledProcessError(
            cmd="pip3 install patroni", returncode=1
        )
        # Add an empty file as Patroni resource just to check that the correct calls were made.
        self.harness.add_resource("patroni", "")
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _inhibit_default_cluster_creation.assert_called_once()
        _install_apt_packages.assert_called_once()
        _install_pip_packages.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch("os.makedirs")
    def test_inhibit_default_cluster_creation(self, _makedirs):
        # Setup a mock for the `open` method.
        mock = mock_open()
        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            self.charm._inhibit_default_cluster_creation()
            _makedirs.assert_called_once_with(
                os.path.dirname(CREATE_CLUSTER_CONF_PATH), mode=0o755, exist_ok=True
            )
            # Check the write calls made to the file.
            handle = mock()
            calls = [
                call("create_main_cluster = false\n"),
                call(f"include '{self.charm._storage_path}/conf.d/postgresql-operator.conf'"),
            ]
            handle.write.assert_has_calls(calls)

    @patch("charm.PostgreSQLProvider.update_endpoints")
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch_network_get(private_address="1.1.1.1")
    def test_on_leader_elected(self, _update_config, _primary_endpoint, _update_endpoints):
        # Assert that there is no password in the peer relation.
        self.assertIsNone(self.charm._peers.data[self.charm.app].get("operator-password", None))

        # Check that a new password was generated on leader election.
        _primary_endpoint.return_value = "1.1.1.1"
        self.harness.set_leader()
        password = self.charm._peers.data[self.charm.app].get("operator-password", None)
        _update_config.assert_called_once()
        _update_endpoints.assert_not_called()
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
        _update_endpoints.assert_called_once()
        self.assertFalse(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Check for a BlockedStatus when there is no primary endpoint.
        _primary_endpoint.return_value = None
        self.harness.set_leader(False)
        self.harness.set_leader()
        _update_endpoints.assert_called_once()  # Assert it was not called again.
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgreSQLProvider.oversee_users")
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch("charm.PostgreSQLProvider.update_endpoints")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.Patroni.member_started")
    @patch("charm.Patroni.bootstrap_cluster")
    @patch("charm.PostgresqlOperatorCharm._replication_password")
    @patch("charm.PostgresqlOperatorCharm._get_password")
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_start(
        self,
        _is_storage_attached,
        _get_password,
        _replication_password,
        _bootstrap_cluster,
        _member_started,
        _,
        __,
        _postgresql,
        _oversee_users,
    ):
        # Test before the passwords are generated.
        _get_password.return_value = None
        self.charm.on.start.emit()
        _bootstrap_cluster.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

        # Mock the passwords.
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"

        # Mock cluster start and postgres user creation success values.
        _bootstrap_cluster.side_effect = [False, True, True]
        _postgresql.create_user.side_effect = [PostgreSQLCreateUserError, None]

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
        self.charm.on.start.emit()
        _postgresql.create_user.assert_called_once()
        _oversee_users.assert_not_called()
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Set an initial waiting status again (like after the install hook was triggered).
        self.harness.model.unit.status = WaitingStatus("fake message")

        # Then test the event of a correct cluster bootstrapping.
        self.charm.on.start.emit()
        self.assertEqual(
            _postgresql.create_user.call_count, 2
        )  # Considering the previous failed call.
        _oversee_users.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

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
        mock_event.set_results.assert_called_once_with({"operator-password": "test-password"})

        # Also test providing the username option.
        mock_event.reset_mock()
        mock_event.params["username"] = "replication"
        self.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with(
            {"replication-password": "replication-test-password"}
        )

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

    @patch("charms.operator_libs_linux.v0.apt.add_package")
    @patch("charms.operator_libs_linux.v0.apt.update")
    def test_install_apt_packages(self, _update, _add_package):
        mock_event = Mock()

        # Mock the returns of apt-get update calls.
        _update.side_effect = [
            subprocess.CalledProcessError(returncode=1, cmd="apt-get update"),
            None,
            None,
        ]

        # Test for problem with apt update.
        with self.assertRaises(subprocess.CalledProcessError):
            self.charm._install_apt_packages(mock_event, ["postgresql"])
            _update.assert_called_once()

        # Test with a not found package.
        _add_package.side_effect = apt.PackageNotFoundError
        with self.assertRaises(apt.PackageNotFoundError):
            self.charm._install_apt_packages(mock_event, ["postgresql"])
            _update.assert_called()
            _add_package.assert_called_with("postgresql")

        # Then test a valid one.
        _update.reset_mock()
        _add_package.reset_mock()
        _add_package.side_effect = None
        self.charm._install_apt_packages(mock_event, ["postgresql"])
        _update.assert_called_once()
        _add_package.assert_called_with("postgresql")

    @patch("subprocess.call")
    def test_install_pip_packages(self, _call):
        # Fake pip packages.
        packages = ["package1", "package2"]

        _call.side_effect = [None, subprocess.SubprocessError]

        # Then test for a succesful install.
        self.charm._install_pip_packages(packages)
        # Check that check_call was invoked with the correct arguments.
        _call.assert_called_once_with(
            [
                "pip3",
                "install",
                "package1 package2",
            ]
        )
        # Assert the status set by the event handler.
        self.assertFalse(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Then, test for an error.
        with self.assertRaises(subprocess.SubprocessError):
            self.charm._install_pip_packages(packages)

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def testget_secret(self, _):
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
            _render_patroni_yml_file.assert_called_once_with(enable_tls=False)
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
            _render_patroni_yml_file.assert_called_once_with(enable_tls=True)
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
