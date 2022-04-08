# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
import subprocess
import unittest
from unittest.mock import Mock, patch

from charms.operator_libs_linux.v0 import apt
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from cluster import (
    ClusterAlreadyRunningError,
    ClusterCreateError,
    ClusterNotRunningError,
    ClusterStartError,
)
from tests.helpers import patch_network_get


class TestCharm(unittest.TestCase):
    @patch_network_get(private_address="1.1.1.1")
    def setUp(self):
        self._peer_relation = "postgresql-replicas"
        self._postgresql_container = "postgresql"
        self._postgresql_service = "postgresql"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm

    @patch("charm.PostgresqlOperatorCharm._install_pip_packages")
    @patch("charm.PostgresqlOperatorCharm._install_apt_packages")
    @patch("charm.PostgresqlCluster.inhibit_default_cluster_creation")
    def test_on_install(
        self, _inhibit_default_cluster_creation, _install_apt_packages, _install_pip_packages
    ):
        # Test without adding Patroni resource.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _inhibit_default_cluster_creation.assert_called_once()
        _install_apt_packages.assert_called_once()
        # Assert that the needed calls were made.
        _install_pip_packages.assert_not_called()
        # Assert the status set by the event handler.
        self.assertEqual(
            self.harness.model.unit.status,
            BlockedStatus("Missing 'patroni' resource"),
        )

        # Add an empty file as Patroni resource just to check that the correct calls were made.
        self.harness.add_resource("patroni", "")
        self.charm.on.install.emit()
        _install_pip_packages.assert_called_once()
        # Assert the status set by the event handler.
        self.assertEqual(
            self.harness.model.unit.status,
            WaitingStatus("waiting to start PostgreSQL"),
        )

    @patch("charm.PostgresqlOperatorCharm._install_pip_packages")
    @patch("charm.PostgresqlOperatorCharm._install_apt_packages")
    @patch("charm.PostgresqlCluster.inhibit_default_cluster_creation")
    def test_on_install_apt_failure(
        self, _inhibit_default_cluster_creation, _install_apt_packages, _install_pip_packages
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

    @patch("charm.PostgresqlOperatorCharm._install_pip_packages")
    @patch("charm.PostgresqlOperatorCharm._install_apt_packages")
    @patch("charm.PostgresqlCluster.inhibit_default_cluster_creation")
    def test_on_install_pip_failure(
        self, _inhibit_default_cluster_creation, _install_apt_packages, _install_pip_packages
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

    def test_on_leader_elected(self):
        # Assert that there is no password in the peer relation.
        self.harness.add_relation(self._peer_relation, self.charm.app.name)
        self.assertIsNone(self.charm._peers.data[self.charm.app].get("postgres-password", None))

        # Check that a new password was generated on leader election.
        self.harness.set_leader()
        password = self.charm._peers.data[self.charm.app].get("postgres-password", None)
        self.assertIsNotNone(password)

        # Trigger a new leader election and check that the password is still the same.
        self.harness.set_leader(False)
        self.harness.set_leader()
        self.assertEqual(
            self.charm._peers.data[self.charm.app].get("postgres-password", None), password
        )

    @patch("charm.PostgresqlCluster.bootstrap_cluster")
    @patch("charm.PostgresqlOperatorCharm._get_postgres_password")
    def test_on_start(self, _get_postgres_password, _bootstrap_cluster):
        # Test before the superuser password is generated.
        _get_postgres_password.return_value = None
        self.charm.on.start.emit()
        _bootstrap_cluster.assert_not_called()
        self.assertEqual(
            self.harness.model.unit.status, WaitingStatus("waiting superuser password generation")
        )

        # Mock the superuser password.
        _get_postgres_password.return_value = "random-password"

        # Test the possible errors.
        errors = [
            {
                "error": ClusterAlreadyRunningError,
                "message": "there is already a running cluster",
            },
            {
                "error": ClusterCreateError("test"),
                "message": "failed to create cluster with error test",
            },
            {
                "error": ClusterNotRunningError("test"),
                "message": "failed to start cluster with error test",
            },
            {
                "error": ClusterStartError("test"),
                "message": "failed to start cluster with error test",
            },
        ]
        for error in errors:
            _bootstrap_cluster.side_effect = error["error"]
            self.charm.on.start.emit()
            _bootstrap_cluster.assert_called_once()
            # Check the correct error message on unit status.
            self.assertEqual(
                self.harness.model.unit.status,
                BlockedStatus(error["message"]),
            )
            # Reset the mock call count.
            _bootstrap_cluster.reset_mock()

        # Then test the event of a correct cluster bootstrapping.
        _bootstrap_cluster.side_effect = None
        self.charm.on.start.emit()
        _bootstrap_cluster.assert_called_once()
        self.assertEqual(
            self.harness.model.unit.status,
            ActiveStatus(),
        )

    @patch("charm.PostgresqlOperatorCharm._get_postgres_password")
    def test_on_get_postgres_password(self, _get_postgres_password):
        mock_event = Mock()
        _get_postgres_password.return_value = "test-password"
        self.charm._on_get_initial_password(mock_event)
        _get_postgres_password.assert_called_once()
        mock_event.set_results.assert_called_once_with({"postgres-password": "test-password"})

    def test_get_postgres_password(self):
        # Test for a None password.
        self.harness.add_relation(self._peer_relation, self.charm.app.name)
        self.assertIsNone(self.charm._get_postgres_password())

        # Then test for a non empty password after leader election and peer data set.
        self.harness.set_leader()
        password = self.charm._get_postgres_password()
        self.assertIsNotNone(password)
        self.assertNotEqual(password, "")

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
        self.assertNotEqual(
            self.harness.model.unit.status,
            BlockedStatus("failed to install pip packages"),
        )

        # Then, test for an error.
        with self.assertRaises(subprocess.SubprocessError):
            self.charm._install_pip_packages(packages)

    def test_new_password(self):
        # Test the password generation twice in order to check if we get different passwords and
        # that they meet the required criteria.
        first_password = self.charm._new_password()
        self.assertEqual(len(first_password), 16)
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password))

        second_password = self.charm._new_password()
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password))
        self.assertNotEqual(second_password, first_password)
