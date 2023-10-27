# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import tenacity
from charms.data_platform_libs.v0.upgrade import ClusterNotReadyError
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import SNAP_PACKAGES
from tests.helpers import patch_network_get


class TestUpgrade(unittest.TestCase):
    """Test the upgrade class."""

    def setUp(self):
        """Set up the test."""
        self.harness = Harness(PostgresqlOperatorCharm)
        self.harness.begin()
        self.upgrade_relation_id = self.harness.add_relation("upgrade", "postgresql")
        self.peer_relation_id = self.harness.add_relation("database-peers", "postgresql")
        for rel_id in (self.upgrade_relation_id, self.peer_relation_id):
            self.harness.add_relation_unit(rel_id, "postgresql/1")
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id, "postgresql/1", {"state": "idle"}
            )
        self.charm = self.harness.charm

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.get_sync_standby_names")
    @patch("charm.Patroni.get_primary")
    def test_build_upgrade_stack(self, _get_primary, _get_sync_standby_names):
        # Set some side effects to test multiple situations.
        _get_primary.side_effect = ["postgresql/0", "postgresql/1"]
        _get_sync_standby_names.side_effect = [["postgresql/1"], ["postgresql/2"]]
        for rel_id in (self.upgrade_relation_id, self.peer_relation_id):
            self.harness.add_relation_unit(rel_id, "postgresql/2")

        self.assertEqual(self.charm.upgrade.build_upgrade_stack(), [0, 1, 2])
        self.assertEqual(self.charm.upgrade.build_upgrade_stack(), [1, 2, 0])

    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("upgrade.logger.info")
    def test_log_rollback(self, mock_logging, _update_config):
        self.charm.upgrade.log_rollback_instructions()
        mock_logging.assert_any_call(
            "Run `juju refresh --revision <previous-revision> postgresql` to initiate the rollback"
        )

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.get_postgresql_version")
    @patch("charms.data_platform_libs.v0.upgrade.DataUpgrade.on_upgrade_changed")
    @patch("charms.data_platform_libs.v0.upgrade.DataUpgrade.set_unit_failed")
    @patch("charms.data_platform_libs.v0.upgrade.DataUpgrade.set_unit_completed")
    @patch("charm.Patroni.is_replication_healthy", new_callable=PropertyMock)
    @patch("charm.Patroni.cluster_members", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("upgrade.wait_fixed", return_value=tenacity.wait_fixed(0))
    @patch("charm.PostgreSQLBackups.start_stop_pgbackrest_service")
    @patch("charm.PostgresqlOperatorCharm._setup_exporter")
    @patch("charm.Patroni.start_patroni")
    @patch("charm.PostgresqlOperatorCharm._install_snap_packages")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    def test_on_upgrade_granted(
        self,
        _update_config,
        _install_snap_packages,
        _start_patroni,
        _setup_exporter,
        _start_stop_pgbackrest_service,
        _,
        _member_started,
        _cluster_members,
        _is_replication_healthy,
        _set_unit_completed,
        _set_unit_failed,
        _on_upgrade_changed,
        __,
    ):
        # Test when the charm fails to start Patroni.
        mock_event = MagicMock()
        _start_patroni.return_value = False
        self.charm.upgrade._on_upgrade_granted(mock_event)
        _update_config.assert_called_once()
        _install_snap_packages.assert_called_once_with(packages=SNAP_PACKAGES, refresh=True)
        _member_started.assert_not_called()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_called_once()
        _on_upgrade_changed.assert_not_called()

        # Test when the member hasn't started yet.
        _set_unit_failed.reset_mock()
        _start_patroni.return_value = True
        _member_started.return_value = False
        self.charm.upgrade._on_upgrade_granted(mock_event)
        self.assertEqual(_member_started.call_count, 6)
        _cluster_members.assert_not_called()
        mock_event.defer.assert_called_once()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()
        _on_upgrade_changed.assert_not_called()

        # Test when the member has already started but not joined the cluster yet.
        _member_started.reset_mock()
        mock_event.defer.reset_mock()
        _member_started.return_value = True
        _cluster_members.return_value = ["postgresql-1"]
        self.charm.upgrade._on_upgrade_granted(mock_event)
        self.assertEqual(_member_started.call_count, 6)
        self.assertEqual(_cluster_members.call_count, 6)
        mock_event.defer.assert_called_once()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()
        _on_upgrade_changed.assert_not_called()

        # Test when the member has already joined the cluster.
        _member_started.reset_mock()
        _cluster_members.reset_mock()
        _set_unit_failed.reset_mock()
        mock_event.defer.reset_mock()
        _cluster_members.return_value = [
            self.charm.unit.name.replace("/", "-"),
            "postgresql-1",
        ]
        self.charm.upgrade._on_upgrade_granted(mock_event)
        _member_started.assert_called_once()
        _cluster_members.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_called_once()
        _set_unit_failed.assert_not_called()
        _on_upgrade_changed.assert_not_called()

        # Test when the member has already joined the cluster but the replication
        # is not healthy yet.
        _set_unit_completed.reset_mock()
        _is_replication_healthy.return_value = False
        self.charm.upgrade._on_upgrade_granted(mock_event)
        mock_event.defer.assert_called_once()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()

        # Test when the member is the leader.
        _member_started.reset_mock()
        _cluster_members.reset_mock()
        mock_event.defer.reset_mock()
        _is_replication_healthy.return_value = True
        with self.harness.hooks_disabled():
            self.harness.set_leader(True)
        self.charm.upgrade._on_upgrade_granted(mock_event)
        _member_started.assert_called_once()
        _cluster_members.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_called_once()
        _set_unit_failed.assert_not_called()
        _on_upgrade_changed.assert_called_once()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.is_creating_backup", new_callable=PropertyMock)
    @patch("charm.Patroni.are_all_members_ready")
    def test_pre_upgrade_check(
        self,
        _are_all_members_ready,
        _is_creating_backup,
    ):
        with self.harness.hooks_disabled():
            self.harness.set_leader(True)

        # Set some side effects to test multiple situations.
        _are_all_members_ready.side_effect = [False, True, True]
        _is_creating_backup.side_effect = [True, False, False]

        # Test when not all members are ready.
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()

        # Test when a backup is being created.
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()

        # Test when everything is ok to start the upgrade.
        self.charm.upgrade.pre_upgrade_check()
