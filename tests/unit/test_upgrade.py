# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest import TestCase
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import tenacity
from charms.data_platform_libs.v0.upgrade import ClusterNotReadyError
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import SNAP_PACKAGES
from tests.helpers import patch_network_get

# used for assert functions
tc = TestCase()


@pytest.fixture(autouse=True)
def harness():
    """Set up the test."""
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    upgrade_relation_id = harness.add_relation("upgrade", "postgresql")
    peer_relation_id = harness.add_relation("database-peers", "postgresql")
    for rel_id in (upgrade_relation_id, peer_relation_id):
        harness.add_relation_unit(rel_id, "postgresql/1")
    with harness.hooks_disabled():
        harness.update_relation_data(upgrade_relation_id, "postgresql/1", {"state": "idle"})
    yield harness
    harness.cleanup()


@patch_network_get(private_address="1.1.1.1")
def test_build_upgrade_stack(harness):
    with (
        patch("charm.Patroni.get_sync_standby_names") as _get_sync_standby_names,
        patch("charm.Patroni.get_primary") as _get_primary,
    ):
        # Set some side effects to test multiple situations.
        _get_primary.side_effect = ["postgresql/0", "postgresql/1"]
        _get_sync_standby_names.side_effect = [["postgresql/1"], ["postgresql/2"]]
        upgrade_relation_id = harness.model.get_relation("upgrade").id
        peer_relation_id = harness.model.get_relation("database-peers").id
        for rel_id in (upgrade_relation_id, peer_relation_id):
            harness.add_relation_unit(rel_id, "postgresql/2")

        tc.assertEqual(harness.charm.upgrade.build_upgrade_stack(), [0, 1, 2])
        tc.assertEqual(harness.charm.upgrade.build_upgrade_stack(), [1, 2, 0])


def test_log_rollback(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("upgrade.logger.info") as mock_logging,
    ):
        harness.charm.upgrade.log_rollback_instructions()
        mock_logging.assert_any_call(
            "Run `juju refresh --revision <previous-revision> postgresql` to initiate the rollback"
        )


@patch_network_get(private_address="1.1.1.1")
def test_on_upgrade_granted(harness):
    with (
        patch("charm.Patroni.get_postgresql_version"),
        patch(
            "charms.data_platform_libs.v0.upgrade.DataUpgrade.on_upgrade_changed"
        ) as _on_upgrade_changed,
        patch(
            "charms.data_platform_libs.v0.upgrade.DataUpgrade.set_unit_failed"
        ) as _set_unit_failed,
        patch(
            "charms.data_platform_libs.v0.upgrade.DataUpgrade.set_unit_completed"
        ) as _set_unit_completed,
        patch(
            "charm.Patroni.is_replication_healthy", new_callable=PropertyMock
        ) as _is_replication_healthy,
        patch("charm.Patroni.cluster_members", new_callable=PropertyMock) as _cluster_members,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("upgrade.wait_fixed", return_value=tenacity.wait_fixed(0)),
        patch(
            "charm.PostgreSQLBackups.start_stop_pgbackrest_service"
        ) as _start_stop_pgbackrest_service,
        patch("charm.PostgresqlOperatorCharm._setup_exporter") as _setup_exporter,
        patch("charm.Patroni.start_patroni") as _start_patroni,
        patch("charm.PostgresqlOperatorCharm._install_snap_packages") as _install_snap_packages,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
    ):
        # Test when the charm fails to start Patroni.
        mock_event = MagicMock()
        _start_patroni.return_value = False
        harness.charm.upgrade._on_upgrade_granted(mock_event)
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
        harness.charm.upgrade._on_upgrade_granted(mock_event)
        tc.assertEqual(_member_started.call_count, 6)
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
        harness.charm.upgrade._on_upgrade_granted(mock_event)
        tc.assertEqual(_member_started.call_count, 6)
        tc.assertEqual(_cluster_members.call_count, 6)
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
            harness.charm.unit.name.replace("/", "-"),
            "postgresql-1",
        ]
        harness.charm.upgrade._on_upgrade_granted(mock_event)
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
        harness.charm.upgrade._on_upgrade_granted(mock_event)
        mock_event.defer.assert_called_once()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()

        # Test when the member is the leader.
        _member_started.reset_mock()
        _cluster_members.reset_mock()
        mock_event.defer.reset_mock()
        _is_replication_healthy.return_value = True
        with harness.hooks_disabled():
            harness.set_leader(True)
        harness.charm.upgrade._on_upgrade_granted(mock_event)
        _member_started.assert_called_once()
        _cluster_members.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_called_once()
        _set_unit_failed.assert_not_called()
        _on_upgrade_changed.assert_called_once()


@patch_network_get(private_address="1.1.1.1")
def test_pre_upgrade_check(harness):
    with (
        patch(
            "charm.Patroni.is_creating_backup", new_callable=PropertyMock
        ) as _is_creating_backup,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
    ):
        with harness.hooks_disabled():
            harness.set_leader(True)

        # Set some side effects to test multiple situations.
        _are_all_members_ready.side_effect = [False, True, True]
        _is_creating_backup.side_effect = [True, False, False]

        # Test when not all members are ready.
        with tc.assertRaises(ClusterNotReadyError):
            harness.charm.upgrade.pre_upgrade_check()

        # Test when a backup is being created.
        with tc.assertRaises(ClusterNotReadyError):
            harness.charm.upgrade.pre_upgrade_check()

        # Test when everything is ok to start the upgrade.
        harness.charm.upgrade.pre_upgrade_check()
