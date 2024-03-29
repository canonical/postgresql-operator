# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import logging
import platform
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, PropertyMock, call, mock_open, patch, sentinel

import pytest
from charms.operator_libs_linux.v2 import snap
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateUserError,
    PostgreSQLEnableDisableExtensionError,
    PostgreSQLUpdateUserPasswordError,
)
from ops import Unit
from ops.framework import EventBase
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    RelationDataTypeError,
    WaitingStatus,
)
from ops.testing import Harness
from parameterized import parameterized
from psycopg2 import OperationalError
from tenacity import RetryError, wait_fixed

from charm import (
    EXTENSIONS_DEPENDENCY_MESSAGE,
    PRIMARY_NOT_REACHABLE_MESSAGE,
    PostgresqlOperatorCharm,
)
from cluster import RemoveRaftMemberFailedError
from constants import PEER, POSTGRESQL_SNAP_NAME, SECRET_INTERNAL_LABEL, SNAP_PACKAGES
from tests.helpers import patch_network_get

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"


# @pytest.mark.usefixtures("juju_has_secrets")
class TestCharm(unittest.TestCase):
    def setUp(self):
        self._peer_relation = PEER
        self._postgresql_container = "postgresql"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm
        self.rel_id = self.harness.add_relation(self._peer_relation, self.charm.app.name)
        self.harness.add_relation("upgrade", self.charm.app.name)

    @pytest.fixture
    def use_caplog(self, caplog):
        self._caplog = caplog

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.subprocess.check_call")
    @patch("charm.snap.SnapCache")
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
        _snap_cache,
        _check_call,
    ):
        # Test without storage.
        self.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()
        pg_snap = _snap_cache.return_value[POSTGRESQL_SNAP_NAME]

        # Test without adding Patroni resource.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once_with(packages=SNAP_PACKAGES)
        assert pg_snap.alias.call_count == 2
        pg_snap.alias.assert_any_call("psql")
        pg_snap.alias.assert_any_call("patronictl")

        assert _check_call.call_count == 3
        _check_call.assert_any_call("mkdir -p /home/snap_daemon".split())
        _check_call.assert_any_call("chown snap_daemon:snap_daemon /home/snap_daemon".split())
        _check_call.assert_any_call("usermod -d /home/snap_daemon snap_daemon".split())

        # Assert the status set by the event handler.
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.logger.exception")
    @patch("charm.subprocess.check_call")
    @patch("charm.snap.SnapCache")
    @patch("charm.PostgresqlOperatorCharm._install_snap_packages")
    @patch("charm.PostgresqlOperatorCharm._reboot_on_detached_storage")
    @patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        side_effect=[False, True, True],
    )
    def test_on_install_failed_to_create_home(
        self,
        _is_storage_attached,
        _reboot_on_detached_storage,
        _install_snap_packages,
        _snap_cache,
        _check_call,
        _logger_exception,
    ):
        # Test without storage.
        self.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()
        pg_snap = _snap_cache.return_value[POSTGRESQL_SNAP_NAME]
        _check_call.side_effect = [subprocess.CalledProcessError(-1, ["test"])]

        # Test without adding Patroni resource.
        self.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once_with(packages=SNAP_PACKAGES)
        assert pg_snap.alias.call_count == 2
        pg_snap.alias.assert_any_call("psql")
        pg_snap.alias.assert_any_call("patronictl")

        _logger_exception.assert_called_once_with("Unable to create snap_daemon home dir")

        # Assert the status set by the event handler.
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

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

    @patch_network_get(private_address="1.1.1.1")
    def test_patroni_scrape_config_no_tls(self):
        result = self.charm.patroni_scrape_config()

        assert result == [
            {
                "metrics_path": "/metrics",
                "scheme": "http",
                "static_configs": [{"targets": ["1.1.1.1:8008"]}],
                "tls_config": {"insecure_skip_verify": True},
            },
        ]

    @patch_network_get(private_address="1.1.1.1")
    @patch(
        "charm.PostgresqlOperatorCharm.is_tls_enabled",
        return_value=True,
        new_callable=PropertyMock,
    )
    def test_patroni_scrape_config_tls(self, _):
        result = self.charm.patroni_scrape_config()

        assert result == [
            {
                "metrics_path": "/metrics",
                "scheme": "https",
                "static_configs": [{"targets": ["1.1.1.1:8008"]}],
                "tls_config": {"insecure_skip_verify": True},
            },
        ]

    @patch(
        "charm.PostgresqlOperatorCharm._units_ips",
        new_callable=PropertyMock,
        return_value={"1.1.1.1", "1.1.1.2"},
    )
    @patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock)
    def test_primary_endpoint(self, _patroni, _):
        _patroni.return_value.get_member_ip.return_value = "1.1.1.1"
        _patroni.return_value.get_primary.return_value = sentinel.primary
        assert self.charm.primary_endpoint == "1.1.1.1"

        _patroni.return_value.get_member_ip.assert_called_once_with(sentinel.primary)
        _patroni.return_value.get_primary.assert_called_once_with()

    @patch("charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=None)
    @patch(
        "charm.PostgresqlOperatorCharm._units_ips",
        new_callable=PropertyMock,
        return_value={"1.1.1.1", "1.1.1.2"},
    )
    @patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock)
    def test_primary_endpoint_no_peers(self, _patroni, _, __):
        assert self.charm.primary_endpoint is None

        assert not _patroni.return_value.get_member_ip.called
        assert not _patroni.return_value.get_primary.called

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

        # Check for a WaitingStatus when the primary is not reachable yet.
        _primary_endpoint.return_value = None
        self.harness.set_leader(False)
        self.harness.set_leader()
        _update_relation_endpoints.assert_called_once()  # Assert it was not called again.
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

    def test_is_cluster_initialised(self):
        # Test when the cluster was not initialised yet.
        self.assertFalse(self.charm.is_cluster_initialised)

        # Test when the cluster was already initialised.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.app.name, {"cluster_initialised": "True"}
            )
        self.assertTrue(self.charm.is_cluster_initialised)

    @patch("charm.PostgresqlOperatorCharm._validate_config_options")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("relations.db.DbProvides.set_up_relation")
    @patch("charm.PostgresqlOperatorCharm.enable_disable_extensions")
    @patch("charm.PostgresqlOperatorCharm.is_cluster_initialised", new_callable=PropertyMock)
    def test_on_config_changed(
        self,
        _is_cluster_initialised,
        _enable_disable_extensions,
        _set_up_relation,
        _update_config,
        _validate_config_options,
    ):
        # Test when the cluster was not initialised yet.
        _is_cluster_initialised.return_value = False
        self.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_not_called()
        _set_up_relation.assert_not_called()

        # Test when the unit is not the leader.
        _is_cluster_initialised.return_value = True
        self.charm.on.config_changed.emit()
        _validate_config_options.assert_called_once()
        _enable_disable_extensions.assert_not_called()
        _set_up_relation.assert_not_called()

        # Test unable to connect to db
        _update_config.reset_mock()
        _validate_config_options.side_effect = OperationalError
        self.charm.on.config_changed.emit()
        assert not _update_config.called
        _validate_config_options.side_effect = None

        # Test after the cluster was initialised.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        self.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_not_called()

        # Test when the unit is in a blocked state due to extensions request,
        # but there are no established legacy relations.
        _enable_disable_extensions.reset_mock()
        self.charm.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        self.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_not_called()

        # Test when the unit is in a blocked state due to extensions request,
        # but there are established legacy relations.
        _enable_disable_extensions.reset_mock()
        _set_up_relation.return_value = False
        db_relation_id = self.harness.add_relation("db", "application")
        self.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_called_once()
        self.harness.remove_relation(db_relation_id)

        _enable_disable_extensions.reset_mock()
        _set_up_relation.reset_mock()
        self.harness.add_relation("db-admin", "application")
        self.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_called_once()

        # Test when  there are established legacy relations,
        # but the charm fails to set up one of them.
        _enable_disable_extensions.reset_mock()
        _set_up_relation.reset_mock()
        _set_up_relation.return_value = False
        self.harness.add_relation("db", "application")
        self.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_called_once()

    @patch("subprocess.check_output", return_value=b"C")
    def test_check_extension_dependencies(self, _):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as _:
            # Test when plugins dependencies exception is not caused
            config = {
                "plugin_address_standardizer_enable": False,
                "plugin_postgis_enable": False,
                "plugin_address_standardizer_data_us_enable": False,
                "plugin_jsonb_plperl_enable": False,
                "plugin_plperl_enable": False,
                "plugin_postgis_raster_enable": False,
                "plugin_postgis_tiger_geocoder_enable": False,
                "plugin_fuzzystrmatch_enable": False,
                "plugin_postgis_topology_enable": False,
            }
            self.harness.update_config(config)
            self.harness.charm.enable_disable_extensions()
            self.assertFalse(isinstance(self.harness.model.unit.status, BlockedStatus))

            # Test when plugins dependencies exception caused
            config["plugin_address_standardizer_enable"] = True
            self.harness.update_config(config)
            self.harness.charm.enable_disable_extensions()
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))
            self.assertEqual(self.harness.model.unit.status.message, EXTENSIONS_DEPENDENCY_MESSAGE)

    @patch("subprocess.check_output", return_value=b"C")
    def test_enable_disable_extensions(self, _):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Test when all extensions install/uninstall succeed.
            postgresql_mock.enable_disable_extension.side_effect = None
            with self.assertNoLogs("charm", "ERROR"):
                self.charm.enable_disable_extensions()
                self.assertEqual(postgresql_mock.enable_disable_extensions.call_count, 1)

            # Test when one extension install/uninstall fails.
            postgresql_mock.reset_mock()
            postgresql_mock.enable_disable_extensions.side_effect = (
                PostgreSQLEnableDisableExtensionError
            )
            with self.assertLogs("charm", "ERROR") as logs:
                self.charm.enable_disable_extensions()
                self.assertEqual(postgresql_mock.enable_disable_extensions.call_count, 1)
                self.assertIn("failed to change plugins", "".join(logs.output))

            # Test when one config option should be skipped (because it's not related
            # to a plugin/extension).
            postgresql_mock.reset_mock()
            postgresql_mock.enable_disable_extensions.side_effect = None
            with self.assertNoLogs("charm", "ERROR"):
                config = """options:
  plugin_citext_enable:
    default: false
    type: boolean
  plugin_hstore_enable:
    default: false
    type: boolean
  plugin_pg_trgm_enable:
    default: false
    type: boolean
  plugin_plpython3u_enable:
    default: false
    type: boolean
  plugin_unaccent_enable:
    default: false
    type: boolean
  plugin_debversion_enable:
    default: false
    type: boolean
  plugin_bloom_enable:
    default: false
    type: boolean
  plugin_btree_gin_enable:
    default: false
    type: boolean
  plugin_btree_gist_enable:
    default: false
    type: boolean
  plugin_cube_enable:
    default: false
    type: boolean
  plugin_dict_int_enable:
    default: false
    type: boolean
  plugin_dict_xsyn_enable:
    default: false
    type: boolean
  plugin_earthdistance_enable:
    default: false
    type: boolean
  plugin_fuzzystrmatch_enable:
    default: false
    type: boolean
  plugin_intarray_enable:
    default: false
    type: boolean
  plugin_isn_enable:
    default: false
    type: boolean
  plugin_lo_enable:
    default: false
    type: boolean
  plugin_ltree_enable:
    default: false
    type: boolean
  plugin_old_snapshot_enable:
    default: false
    type: boolean
  plugin_pg_freespacemap_enable:
    default: false
    type: boolean
  plugin_pgrowlocks_enable:
    default: false
    type: boolean
  plugin_pgstattuple_enable:
    default: false
    type: boolean
  plugin_pg_visibility_enable:
    default: false
    type: boolean
  plugin_seg_enable:
    default: false
    type: boolean
  plugin_tablefunc_enable:
    default: false
    type: boolean
  plugin_tcn_enable:
    default: false
    type: boolean
  plugin_tsm_system_rows_enable:
    default: false
    type: boolean
  plugin_tsm_system_time_enable:
    default: false
    type: boolean
  plugin_uuid_ossp_enable:
    default: false
    type: boolean
  plugin_spi_enable:
    default: false
    type: boolean
  plugin_bool_plperl_enable:
    default: false
    type: boolean
  plugin_hll_enable:
    default: false
    type: boolean
  plugin_hypopg_enable:
    default: false
    type: boolean
  plugin_ip4r_enable:
    default: false
    type: boolean
  plugin_plperl_enable:
    default: false
    type: boolean
  plugin_jsonb_plperl_enable:
    default: false
    type: boolean
  plugin_orafce_enable:
    default: false
    type: boolean
  plugin_pg_similarity_enable:
    default: false
    type: boolean
  plugin_prefix_enable:
    default: false
    type: boolean
  plugin_rdkit_enable:
    default: false
    type: boolean
  plugin_tds_fdw_enable:
    default: false
    type: boolean
  plugin_icu_ext_enable:
    default: false
    type: boolean
  plugin_pltcl_enable:
    default: false
    type: boolean
  plugin_postgis_enable:
    default: false
    type: boolean
  plugin_postgis_raster_enable:
    default: false
    type: boolean
  plugin_address_standardizer_enable:
    default: false
    type: boolean
  plugin_address_standardizer_data_us_enable:
    default: false
    type: boolean
  plugin_postgis_tiger_geocoder_enable:
    default: false
    type: boolean
  plugin_postgis_topology_enable:
    default: false
    type: boolean
  plugin_vector_enable:
    default: false
    type: boolean
  profile:
    default: production
    type: string"""
                harness = Harness(PostgresqlOperatorCharm, config=config)
                self.addCleanup(harness.cleanup)
                harness.begin()
                harness.charm.enable_disable_extensions()
                self.assertEqual(postgresql_mock.enable_disable_extensions.call_count, 1)

    @patch("charm.PostgresqlOperatorCharm.enable_disable_extensions")
    @patch("charm.snap.SnapCache")
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
    @patch("upgrade.PostgreSQLUpgrade.idle", return_value=True)
    @patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        side_effect=[False, True, True, True, True],
    )
    def test_on_start(
        self,
        _is_storage_attached,
        _idle,
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
        _snap_cache,
        _enable_disable_extensions,
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
        _postgresql.create_user.side_effect = [PostgreSQLCreateUserError, None, None, None]

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
            _postgresql.create_user.call_count, 4
        )  # Considering the previous failed call.
        _oversee_users.assert_called_once()
        _enable_disable_extensions.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

    @patch("charm.snap.SnapCache")
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
    @patch("upgrade.PostgreSQLUpgrade.idle", return_value=True)
    @patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        return_value=True,
    )
    def test_on_start_replica(
        self,
        _is_storage_attached,
        _idle,
        _get_password,
        _replication_password,
        _defer,
        _update_relation_endpoints,
        _member_started,
        _configure_patroni_on_unit,
        _get_postgresql_version,
        _snap_cache,
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
    @patch("subprocess.check_output", return_value=b"C")
    @patch("charm.snap.SnapCache")
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch("charm.Patroni")
    @patch("charm.PostgresqlOperatorCharm._get_password")
    @patch("upgrade.PostgreSQLUpgrade.idle", return_value=True)
    @patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True)
    def test_on_start_no_patroni_member(
        self,
        _is_storage_attached,
        _idle,
        _get_password,
        patroni,
        _postgresql,
        _snap_cache,
        _,
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

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    def test_on_get_password(self, _):
        # Create a mock event and set passwords in peer relation data.
        self.harness.set_leader(True)
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

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.ClusterTopologyObserver.start_observer")
    @patch("charm.PostgresqlOperatorCharm._set_primary_status_message")
    @patch("charm.Patroni.restart_patroni")
    @patch("charm.Patroni.is_member_isolated")
    @patch("charm.Patroni.reinitialize_postgresql")
    @patch("charm.Patroni.member_replication_lag", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock(return_value=True),
    )
    @patch("charm.PostgreSQLProvider.oversee_users")
    @patch("upgrade.PostgreSQLUpgrade.idle", return_value=True)
    def test_on_update_status(
        self,
        _,
        _oversee_users,
        _primary_endpoint,
        _update_relation_endpoints,
        _member_started,
        _member_replication_lag,
        _reinitialize_postgresql,
        _is_member_isolated,
        _restart_patroni,
        _set_primary_status_message,
        _start_observer,
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
        _start_observer.assert_called_once()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.ClusterTopologyObserver.start_observer")
    @patch("charm.PostgresqlOperatorCharm._set_primary_status_message")
    @patch("charm.PostgresqlOperatorCharm._handle_workload_failures")
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock(return_value=True),
    )
    @patch("charm.PostgreSQLProvider.oversee_users")
    @patch("charm.PostgresqlOperatorCharm._handle_processes_failures")
    @patch("charm.PostgreSQLBackups.can_use_s3_repository")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.Patroni.get_member_status")
    @patch("upgrade.PostgreSQLUpgrade.idle", return_value=True)
    def test_on_update_status_after_restore_operation(
        self,
        _,
        _get_member_status,
        _member_started,
        _update_config,
        _can_use_s3_repository,
        _handle_processes_failures,
        _oversee_users,
        _primary_endpoint,
        _update_relation_endpoints,
        _handle_workload_failures,
        _set_primary_status_message,
        __,
    ):
        # Test when the restore operation fails.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.app.name,
                {"cluster_initialised": "True", "restoring-backup": "2023-01-01T09:00:00Z"},
            )
        _get_member_status.return_value = "failed"
        self.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _handle_workload_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)

        # Test when the restore operation hasn't finished yet.
        self.charm.unit.status = ActiveStatus()
        _get_member_status.return_value = "running"
        _member_started.return_value = False
        self.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _handle_workload_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Assert that the backup id is still in the application relation databag.
        self.assertEqual(
            self.harness.get_relation_data(self.rel_id, self.charm.app),
            {"cluster_initialised": "True", "restoring-backup": "2023-01-01T09:00:00Z"},
        )

        # Test when the restore operation finished successfully.
        _member_started.return_value = True
        _can_use_s3_repository.return_value = (True, None)
        _handle_processes_failures.return_value = False
        _handle_workload_failures.return_value = False
        self.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_called_once()
        _oversee_users.assert_called_once()
        _update_relation_endpoints.assert_called_once()
        _handle_workload_failures.assert_called_once()
        _set_primary_status_message.assert_called_once()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Assert that the backup id is not in the application relation databag anymore.
        self.assertEqual(
            self.harness.get_relation_data(self.rel_id, self.charm.app),
            {"cluster_initialised": "True"},
        )

        # Test when it's not possible to use the configured S3 repository.
        _update_config.reset_mock()
        _handle_processes_failures.reset_mock()
        _oversee_users.reset_mock()
        _update_relation_endpoints.reset_mock()
        _handle_workload_failures.reset_mock()
        _set_primary_status_message.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.app.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        _can_use_s3_repository.return_value = (False, "fake validation message")
        self.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _handle_workload_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)
        self.assertEqual(self.charm.unit.status.message, "fake validation message")

        # Assert that the backup id is not in the application relation databag anymore.
        self.assertEqual(
            self.harness.get_relation_data(self.rel_id, self.charm.app),
            {"cluster_initialised": "True"},
        )

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
        self.charm._install_snap_packages([
            ("postgresql", {"revision": {platform.machine(): "42"}})
        ])
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_package.ensure.assert_called_once_with(
            snap.SnapState.Latest, revision="42", channel=""
        )
        _snap_package.hold.assert_called_once_with()

        # Test with refresh
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.present = True
        self.charm._install_snap_packages(
            [("postgresql", {"revision": {platform.machine(): "42"}, "channel": "latest/test"})],
            refresh=True,
        )
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_package.ensure.assert_called_once_with(
            snap.SnapState.Latest, revision="42", channel="latest/test"
        )
        _snap_package.hold.assert_called_once_with()

        # Test without refresh
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        self.charm._install_snap_packages([
            ("postgresql", {"revision": {platform.machine(): "42"}})
        ])
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_package.ensure.assert_not_called()
        _snap_package.hold.assert_not_called()

        # test missing architecture
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.present = True
        with self.assertRaises(KeyError):
            self.charm._install_snap_packages(
                [("postgresql", {"revision": {"missingarch": "42"}})],
                refresh=True,
            )
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        assert not _snap_package.ensure.called
        assert not _snap_package.hold.called

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
    @patch("charm.Patroni.are_all_members_ready")
    def test_restart(self, _are_all_members_ready, _restart_postgresql):
        _are_all_members_ready.side_effect = [False, True, True]

        # Test when not all members are ready.
        mock_event = MagicMock()
        self.charm._restart(mock_event)
        mock_event.defer.assert_called_once()
        _restart_postgresql.assert_not_called()

        # Test a successful restart.
        mock_event.defer.reset_mock()
        self.charm._restart(mock_event)
        self.assertFalse(isinstance(self.charm.unit.status, BlockedStatus))
        mock_event.defer.assert_not_called()

        # Test a failed restart.
        _restart_postgresql.side_effect = RetryError(last_attempt=1)
        self.charm._restart(mock_event)
        self.assertTrue(isinstance(self.charm.unit.status, BlockedStatus))
        mock_event.defer.assert_not_called()

    @patch_network_get(private_address="1.1.1.1")
    @patch("subprocess.check_output", return_value=b"C")
    @patch("charm.snap.SnapCache")
    @patch("charm.PostgresqlOperatorCharm._handle_postgresql_restart_need")
    @patch("charm.Patroni.bulk_update_parameters_controller_by_patroni")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm._is_workload_running", new_callable=PropertyMock)
    @patch("charm.Patroni.render_patroni_yml_file")
    @patch("charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock)
    def test_update_config(
        self,
        _is_tls_enabled,
        _render_patroni_yml_file,
        _is_workload_running,
        _member_started,
        _,
        _handle_postgresql_restart_need,
        __,
        ___,
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Mock some properties.
            postgresql_mock.is_tls_enabled = PropertyMock(side_effect=[False, False, False, False])
            _is_workload_running.side_effect = [False, False, True, True, False, True]
            _member_started.side_effect = [True, True, False]
            postgresql_mock.build_postgresql_parameters.return_value = {"test": "test"}

            # Test when only one of the two config options for profile limit memory is set.
            self.harness.update_config({"profile-limit-memory": 1000})
            self.charm.update_config()

            # Test when only one of the two config options for profile limit memory is set.
            self.harness.update_config(
                {"profile_limit_memory": 1000}, unset={"profile-limit-memory"}
            )
            self.charm.update_config()

            # Test when the two config options for profile limit memory are set at the same time.
            _render_patroni_yml_file.reset_mock()
            self.harness.update_config({"profile-limit-memory": 1000})
            with self.assertRaises(ValueError):
                self.charm.update_config()

            # Test without TLS files available.
            self.harness.update_config(unset={"profile-limit-memory", "profile_limit_memory"})
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(self.rel_id, self.charm.unit.name, {"tls": ""})
            _is_tls_enabled.return_value = False
            self.charm.update_config()
            _render_patroni_yml_file.assert_called_once_with(
                connectivity=True,
                is_creating_backup=False,
                enable_tls=False,
                backup_id=None,
                stanza=None,
                restore_stanza=None,
                parameters={"test": "test"},
            )
            _handle_postgresql_restart_need.assert_called_once_with(False)
            self.assertNotIn(
                "tls", self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
            )

            # Test with TLS files available.
            _handle_postgresql_restart_need.reset_mock()
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"tls": ""}
            )  # Mock some data in the relation to test that it change.
            _is_tls_enabled.return_value = True
            _render_patroni_yml_file.reset_mock()
            self.charm.update_config()
            _render_patroni_yml_file.assert_called_once_with(
                connectivity=True,
                is_creating_backup=False,
                enable_tls=True,
                backup_id=None,
                stanza=None,
                restore_stanza=None,
                parameters={"test": "test"},
            )
            _handle_postgresql_restart_need.assert_called_once()
            self.assertNotIn(
                "tls",
                self.harness.get_relation_data(
                    self.rel_id, self.charm.unit.name
                ),  # The "tls" flag is set in handle_postgresql_restart_need.
            )

            # Test with workload not running yet.
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"tls": ""}
            )  # Mock some data in the relation to test that it change.
            _handle_postgresql_restart_need.reset_mock()
            self.charm.update_config()
            _handle_postgresql_restart_need.assert_not_called()
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.charm.unit.name)["tls"], "enabled"
            )

            # Test with member not started yet.
            self.harness.update_relation_data(
                self.rel_id, self.charm.unit.name, {"tls": ""}
            )  # Mock some data in the relation to test that it doesn't change.
            self.charm.update_config()
            _handle_postgresql_restart_need.assert_not_called()
            self.assertNotIn(
                "tls", self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
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
        self.harness.model.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

        self.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_not_called()
        _primary_endpoint.assert_called_once_with()
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))
        self.assertEqual(self.harness.model.unit.status.message, PRIMARY_NOT_REACHABLE_MESSAGE)

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
        return_value="fake-unit",
    )
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    def test_on_cluster_topology_change_clear_blocked(
        self, _update_relation_endpoints, _primary_endpoint
    ):
        self.harness.model.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

        self.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_called_once_with()
        _primary_endpoint.assert_called_once_with()
        self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

    @patch("charm.PostgresqlOperatorCharm.postgresql", new_callable=PropertyMock)
    @patch("config.subprocess")
    def test_validate_config_options(self, _, _charm_lib):
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = []
        _charm_lib.return_value.validate_date_style.return_value = []
        _charm_lib.return_value.get_postgresql_timezones.return_value = []

        # Test instance_default_text_search_config exception
        with self.harness.hooks_disabled():
            self.harness.update_config({"instance_default_text_search_config": "pg_catalog.test"})

        with self.assertRaises(ValueError) as e:
            self.charm._validate_config_options()
            assert (
                e.msg == "instance_default_text_search_config config option has an invalid value"
            )

        _charm_lib.return_value.get_postgresql_text_search_configs.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = [
            "pg_catalog.test"
        ]

        # Test request_date_style exception
        with self.harness.hooks_disabled():
            self.harness.update_config({"request_date_style": "ISO, TEST"})

        with self.assertRaises(ValueError) as e:
            self.charm._validate_config_options()
            assert e.msg == "request_date_style config option has an invalid value"

        _charm_lib.return_value.validate_date_style.assert_called_once_with("ISO, TEST")
        _charm_lib.return_value.validate_date_style.return_value = ["ISO, TEST"]

        # Test request_time_zone exception
        with self.harness.hooks_disabled():
            self.harness.update_config({"request_time_zone": "TEST_ZONE"})

        with self.assertRaises(ValueError) as e:
            self.charm._validate_config_options()
            assert e.msg == "request_time_zone config option has an invalid value"

        _charm_lib.return_value.get_postgresql_timezones.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_timezones.return_value = ["TEST_ZONE"]

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.snap.SnapCache")
    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch("charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock)
    @patch("backups.PostgreSQLBackups.check_stanza")
    @patch("backups.PostgreSQLBackups.coordinate_stanza_fields")
    @patch("backups.PostgreSQLBackups.start_stop_pgbackrest_service")
    @patch("charm.Patroni.reinitialize_postgresql")
    @patch("charm.Patroni.member_replication_lag", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.is_primary")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.Patroni.start_patroni")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgresqlOperatorCharm._update_member_ip")
    @patch("charm.PostgresqlOperatorCharm._reconfigure_cluster")
    @patch("ops.framework.EventBase.defer")
    def test_on_peer_relation_changed(
        self,
        _defer,
        _reconfigure_cluster,
        _update_member_ip,
        _update_config,
        _start_patroni,
        _member_started,
        _is_primary,
        _member_replication_lag,
        _reinitialize_postgresql,
        _start_stop_pgbackrest_service,
        _coordinate_stanza_fields,
        _check_stanza,
        _primary_endpoint,
        _update_relation_endpoints,
        _,
    ):
        # Test an uninitialized cluster.
        mock_event = Mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.app.name, {"cluster_initialised": ""}
            )
        self.charm._on_peer_relation_changed(mock_event)
        mock_event.defer.assert_called_once()
        _reconfigure_cluster.assert_not_called()

        # Test an initialized cluster and this is the leader unit
        # (but it fails to reconfigure the cluster).
        mock_event.defer.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.app.name,
                {"cluster_initialised": "True", "members_ips": '["1.1.1.1"]'},
            )
            self.harness.set_leader()
        _reconfigure_cluster.return_value = False
        self.charm._on_peer_relation_changed(mock_event)
        _reconfigure_cluster.assert_called_once_with(mock_event)
        mock_event.defer.assert_called_once()

        # Test when the leader can reconfigure the cluster.
        mock_event.defer.reset_mock()
        _reconfigure_cluster.reset_mock()
        _reconfigure_cluster.return_value = True
        _update_member_ip.return_value = False
        _member_started.return_value = True
        _primary_endpoint.return_value = "1.1.1.1"
        self.harness.model.unit.status = WaitingStatus("awaiting for cluster to start")
        self.charm._on_peer_relation_changed(mock_event)
        mock_event.defer.assert_not_called()
        _reconfigure_cluster.assert_called_once_with(mock_event)
        _update_member_ip.assert_called_once()
        _update_config.assert_called_once()
        _start_patroni.assert_called_once()
        _update_relation_endpoints.assert_called_once()
        self.assertIsInstance(self.harness.model.unit.status, ActiveStatus)

        # Test when the cluster member updates its IP.
        _update_member_ip.reset_mock()
        _update_config.reset_mock()
        _start_patroni.reset_mock()
        _update_relation_endpoints.reset_mock()
        _update_member_ip.return_value = True
        self.charm._on_peer_relation_changed(mock_event)
        _update_member_ip.assert_called_once()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        _update_relation_endpoints.assert_not_called()

        # Test when the unit fails to update the Patroni configuration.
        _update_member_ip.return_value = False
        _update_config.side_effect = RetryError(last_attempt=1)
        self.charm._on_peer_relation_changed(mock_event)
        _update_config.assert_called_once()
        _start_patroni.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

        # Test when Patroni hasn't started yet in the unit.
        _update_config.side_effect = None
        _member_started.return_value = False
        self.charm._on_peer_relation_changed(mock_event)
        _start_patroni.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.harness.model.unit.status, WaitingStatus)

        # Test when Patroni has already started but this is a replica with a
        # huge or unknown lag.
        self.relation = self.harness.model.get_relation(self._peer_relation, self.rel_id)
        _member_started.return_value = True
        for values in itertools.product([True, False], ["0", "1000", "1001", "unknown"]):
            _defer.reset_mock()
            _check_stanza.reset_mock()
            _start_stop_pgbackrest_service.reset_mock()
            _is_primary.return_value = values[0]
            _member_replication_lag.return_value = values[1]
            self.charm.unit.status = ActiveStatus()
            self.charm.on.database_peers_relation_changed.emit(self.relation)
            if _is_primary.return_value == values[0] or int(values[1]) <= 1000:
                _defer.assert_not_called()
                _check_stanza.assert_called_once()
                _start_stop_pgbackrest_service.assert_called_once()
                self.assertIsInstance(self.charm.unit.status, ActiveStatus)
            else:
                _defer.assert_called_once()
                _check_stanza.assert_not_called()
                _start_stop_pgbackrest_service.assert_not_called()
                self.assertIsInstance(self.charm.unit.status, MaintenanceStatus)

        # Test when it was not possible to start the pgBackRest service yet.
        self.relation = self.harness.model.get_relation(self._peer_relation, self.rel_id)
        _member_started.return_value = True
        _defer.reset_mock()
        _coordinate_stanza_fields.reset_mock()
        _check_stanza.reset_mock()
        _start_stop_pgbackrest_service.return_value = False
        self.charm.on.database_peers_relation_changed.emit(self.relation)
        _defer.assert_called_once()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()

        # Test the last calls been made when it was possible to start the
        # pgBackRest service.
        _defer.reset_mock()
        _start_stop_pgbackrest_service.return_value = True
        self.charm.on.database_peers_relation_changed.emit(self.relation)
        _defer.assert_not_called()
        _coordinate_stanza_fields.assert_called_once()
        _check_stanza.assert_called_once()

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
        ip_to_remove = "1.1.1.1"
        relation_data = {mock_event.unit: {"ip-to-remove": ip_to_remove}}
        mock_event.relation.data = relation_data
        self.assertFalse(self.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_called_once_with(ip_to_remove)
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_not_called()

        # Test when a change is needed in the member IP, and it succeeds
        # (but the old IP was already been removed).
        _remove_raft_member.reset_mock()
        _remove_raft_member.side_effect = None
        _add_members.reset_mock()
        mock_event.relation.data = relation_data
        self.assertTrue(self.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_called_once_with(ip_to_remove)
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_called_once_with(mock_event)

        # Test when the old IP wasn't removed yet.
        _remove_raft_member.reset_mock()
        _add_members.reset_mock()
        mock_event.relation.data = relation_data
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.app.name, {"members_ips": '["' + ip_to_remove + '"]'}
            )
        self.assertTrue(self.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_called_once_with(ip_to_remove)
        _remove_from_members_ips.assert_called_once_with(ip_to_remove)
        _add_members.assert_called_once_with(mock_event)

    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=False)
    def test_update_certificate(self, _, _request_certificate):
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

        self.harness.charm.get_secret("unit", "ca") == ca
        self.harness.charm.get_secret("unit", "cert") == cert
        self.harness.charm.get_secret("unit", "key") == key
        self.harness.charm.get_secret("unit", "private-key") == private_key

    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_update_certificate_secrets(self, _, _request_certificate):
        # If there is no current TLS files, _request_certificate should be called
        # only when the certificates relation is established.
        self.charm._update_certificate()
        _request_certificate.assert_not_called()

        # Test with already present TLS files (when they will be replaced by new ones).
        ca = "fake CA"
        cert = "fake certificate"
        key = private_key = "fake private key"
        self.harness.charm.set_secret("unit", "ca", ca)
        self.harness.charm.set_secret("unit", "cert", cert)
        self.harness.charm.set_secret("unit", "key", key)
        self.harness.charm.set_secret("unit", "private-key", private_key)

        self.charm._update_certificate()
        _request_certificate.assert_called_once_with(private_key)

        self.harness.charm.get_secret("unit", "ca") == ca
        self.harness.charm.get_secret("unit", "cert") == cert
        self.harness.charm.get_secret("unit", "key") == key
        self.harness.charm.get_secret("unit", "private-key") == private_key

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

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.Patroni.render_file")
    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS.get_tls_files")
    def test_push_tls_files_to_workload(self, _get_tls_files, _render_file, _update_config):
        _get_tls_files.side_effect = [
            ("key", "ca", "cert"),
            ("key", "ca", None),
            ("key", None, "cert"),
            (None, "ca", "cert"),
        ]
        _update_config.side_effect = [True, False, False, False]

        # Test when all TLS files are available.
        self.assertTrue(self.charm.push_tls_files_to_workload())
        self.assertEqual(_render_file.call_count, 3)

        # Test when not all TLS files are available.
        for _ in range(3):
            _render_file.reset_mock()
            self.assertFalse(self.charm.push_tls_files_to_workload())
            self.assertEqual(_render_file.call_count, 2)

    @patch("charm.snap.SnapCache")
    def test_is_workload_running(self, _snap_cache):
        pg_snap = _snap_cache.return_value[POSTGRESQL_SNAP_NAME]

        pg_snap.present = False
        self.assertFalse(self.charm._is_workload_running)

        pg_snap.present = True
        self.assertTrue(self.charm._is_workload_running)

    def test_get_available_memory(self):
        meminfo = (
            "MemTotal:       16089488 kB"
            "MemFree:          799284 kB"
            "MemAvailable:    3926924 kB"
            "Buffers:          187232 kB"
            "Cached:          4445936 kB"
            "SwapCached:       156012 kB"
            "Active:         11890336 kB"
        )

        with patch("builtins.open", mock_open(read_data=meminfo)):
            self.assertEqual(self.charm.get_available_memory(), 16475635712)

        with patch("builtins.open", mock_open(read_data="")):
            self.assertEqual(self.charm.get_available_memory(), 0)

    @patch("charm.ClusterTopologyObserver")
    @patch("charm.JujuVersion")
    def test_juju_run_exec_divergence(self, _juju_version: Mock, _topology_observer: Mock):
        # Juju 2
        _juju_version.from_environ.return_value.major = 2
        harness = Harness(PostgresqlOperatorCharm)
        harness.begin()
        _topology_observer.assert_called_once_with(harness.charm, "/usr/bin/juju-run")
        _topology_observer.reset_mock()

        # Juju 3
        _juju_version.from_environ.return_value.major = 3
        harness = Harness(PostgresqlOperatorCharm)
        harness.begin()
        _topology_observer.assert_called_once_with(harness.charm, "/usr/bin/juju-exec")

    def test_client_relations(self):
        # Test when the charm has no relations.
        self.assertEqual(self.charm.client_relations, [])

        # Test when the charm has some relations.
        self.harness.add_relation("database", "application")
        self.harness.add_relation("db", "legacy-application")
        self.harness.add_relation("db-admin", "legacy-admin-application")
        database_relation = self.harness.model.get_relation("database")
        db_relation = self.harness.model.get_relation("db")
        db_admin_relation = self.harness.model.get_relation("db-admin")
        self.assertEqual(
            self.charm.client_relations, [database_relation, db_relation, db_admin_relation]
        )

    #
    # Secrets
    #

    def test_scope_obj(self):
        assert self.charm._scope_obj("app") == self.charm.framework.model.app
        assert self.charm._scope_obj("unit") == self.charm.framework.model.unit
        assert self.charm._scope_obj("test") is None

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_get_secret(self, _):
        # App level changes require leader privileges
        self.harness.set_leader()
        # Test application scope.
        assert self.charm.get_secret("app", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {"password": "test-password"}
        )
        assert self.charm.get_secret("app", "password") == "test-password"

        # Unit level changes don't require leader privileges
        self.harness.set_leader(False)
        # Test unit scope.
        assert self.charm.get_secret("unit", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.unit.name, {"password": "test-password"}
        )
        assert self.charm.get_secret("unit", "password") == "test-password"

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_on_get_password_secrets(self, mock1, mock2):
        # Create a mock event and set passwords in peer relation data.
        self.harness.set_leader()
        mock_event = MagicMock(params={})
        self.harness.charm.set_secret("app", "operator-password", "test-password")
        self.harness.charm.set_secret("app", "replication-password", "replication-test-password")

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

    @parameterized.expand([("app"), ("unit")])
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_get_secret_secrets(self, scope, _, __):
        self.harness.set_leader()

        assert self.charm.get_secret(scope, "operator-password") is None
        self.charm.set_secret(scope, "operator-password", "test-password")
        assert self.charm.get_secret(scope, "operator-password") == "test-password"

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
        self.charm.set_secret("app", "password", None)
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.app.name)

        # Test unit scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
        self.charm.set_secret("unit", "password", "test-password")
        assert (
            self.harness.get_relation_data(self.rel_id, self.charm.unit.name)["password"]
            == "test-password"
        )
        self.charm.set_secret("unit", "password", None)
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.unit.name)

        with self.assertRaises(RuntimeError):
            self.charm.set_secret("test", "password", "test")

    @parameterized.expand([("app", True), ("unit", True), ("unit", False)])
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_set_reset_new_secret(self, scope, is_leader, _, __):
        """NOTE: currently ops.testing seems to allow for non-leader to set secrets too!"""
        # App has to be leader, unit can be either
        self.harness.set_leader(is_leader)
        # Getting current password
        self.harness.charm.set_secret(scope, "new-secret", "bla")
        assert self.harness.charm.get_secret(scope, "new-secret") == "bla"

        # Reset new secret
        self.harness.charm.set_secret(scope, "new-secret", "blablabla")
        assert self.harness.charm.get_secret(scope, "new-secret") == "blablabla"

        # Set another new secret
        self.harness.charm.set_secret(scope, "new-secret2", "blablabla")
        assert self.harness.charm.get_secret(scope, "new-secret2") == "blablabla"

    @parameterized.expand([("app", True), ("unit", True), ("unit", False)])
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_invalid_secret(self, scope, is_leader, _, __):
        # App has to be leader, unit can be either
        self.harness.set_leader(is_leader)

        with self.assertRaises(RelationDataTypeError):
            self.harness.charm.set_secret(scope, "somekey", 1)

        self.harness.charm.set_secret(scope, "somekey", "")
        assert self.harness.charm.get_secret(scope, "somekey") is None

    @pytest.mark.usefixtures("use_caplog")
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_delete_password(self, _):
        """NOTE: currently ops.testing seems to allow for non-leader to remove secrets too!"""
        self.harness.set_leader(True)
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {"replication": "somepw"}
        )
        self.harness.charm.remove_secret("app", "replication")
        assert self.harness.charm.get_secret("app", "replication") is None

        self.harness.set_leader(False)
        self.harness.update_relation_data(
            self.rel_id, self.charm.unit.name, {"somekey": "somevalue"}
        )
        self.harness.charm.remove_secret("unit", "somekey")
        assert self.harness.charm.get_secret("unit", "somekey") is None

        self.harness.set_leader(True)
        with self._caplog.at_level(logging.ERROR):
            self.harness.charm.remove_secret("app", "replication")
            assert (
                "Non-existing field 'replication' was attempted to be removed" in self._caplog.text
            )

            self.harness.charm.remove_secret("unit", "somekey")
            assert "Non-existing field 'somekey' was attempted to be removed" in self._caplog.text

            self.harness.charm.remove_secret("app", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in self._caplog.text
            )

            self.harness.charm.remove_secret("unit", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in self._caplog.text
            )

    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @pytest.mark.usefixtures("use_caplog")
    def test_delete_existing_password_secrets(self, _, __):
        """NOTE: currently ops.testing seems to allow for non-leader to remove secrets too!"""
        self.harness.set_leader(True)
        self.harness.charm.set_secret("app", "operator-password", "somepw")
        self.harness.charm.remove_secret("app", "operator-password")
        assert self.harness.charm.get_secret("app", "operator-password") is None

        self.harness.set_leader(False)
        self.harness.charm.set_secret("unit", "operator-password", "somesecret")
        self.harness.charm.remove_secret("unit", "operator-password")
        assert self.harness.charm.get_secret("unit", "operator-password") is None

        self.harness.set_leader(True)
        with self._caplog.at_level(logging.ERROR):
            self.harness.charm.remove_secret("app", "operator-password")
            assert (
                "Non-existing secret operator-password was attempted to be removed."
                in self._caplog.text
            )

            self.harness.charm.remove_secret("unit", "operator-password")
            assert (
                "Non-existing secret operator-password was attempted to be removed."
                in self._caplog.text
            )

            self.harness.charm.remove_secret("app", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in self._caplog.text
            )

            self.harness.charm.remove_secret("unit", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in self._caplog.text
            )

    @parameterized.expand([("app", True), ("unit", True), ("unit", False)])
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_migration_from_databag(self, scope, is_leader, _, __):
        """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage."""
        # App has to be leader, unit can be either
        self.harness.set_leader(is_leader)

        # Getting current password
        entity = getattr(self.charm, scope)
        self.harness.update_relation_data(self.rel_id, entity.name, {"operator-password": "bla"})
        assert self.harness.charm.get_secret(scope, "operator-password") == "bla"

        # Reset new secret
        self.harness.charm.set_secret(scope, "operator-password", "blablabla")
        assert self.harness.charm.model.get_secret(label=f"postgresql.{scope}")
        assert self.harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert "operator-password" not in self.harness.get_relation_data(
            self.rel_id, getattr(self.charm, scope).name
        )

    @parameterized.expand([("app", True), ("unit", True), ("unit", False)])
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    def test_migration_from_single_secret(self, scope, is_leader, _, __):
        """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage."""
        # App has to be leader, unit can be either
        self.harness.set_leader(is_leader)

        secret = self.harness.charm.app.add_secret({"operator-password": "bla"})

        # Getting current password
        entity = getattr(self.charm, scope)
        self.harness.update_relation_data(
            self.rel_id, entity.name, {SECRET_INTERNAL_LABEL: secret.id}
        )
        assert self.harness.charm.get_secret(scope, "operator-password") == "bla"

        # Reset new secret
        # Only the leader can set app secret content.
        with self.harness.hooks_disabled():
            self.harness.set_leader(True)
        self.harness.charm.set_secret(scope, "operator-password", "blablabla")
        with self.harness.hooks_disabled():
            self.harness.set_leader(is_leader)
        assert self.harness.charm.model.get_secret(label=f"postgresql.{scope}")
        assert self.harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert SECRET_INTERNAL_LABEL not in self.harness.get_relation_data(
            self.rel_id, getattr(self.charm, scope).name
        )

    @patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock")
    @patch("charm.wait_fixed", return_value=wait_fixed(0))
    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._unit_ip")
    @patch("charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock)
    def test_handle_postgresql_restart_need(
        self, _is_tls_enabled, _, _reload_patroni_configuration, __, _restart
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            for values in itertools.product(
                [True, False], [True, False], [True, False], [True, False], [True, False]
            ):
                _reload_patroni_configuration.reset_mock()
                _restart.reset_mock()
                with self.harness.hooks_disabled():
                    self.harness.update_relation_data(
                        self.rel_id, self.charm.unit.name, {"tls": ""}
                    )
                    self.harness.update_relation_data(
                        self.rel_id,
                        self.charm.unit.name,
                        {"postgresql_restarted": ("True" if values[4] else "")},
                    )

                _is_tls_enabled.return_value = values[1]
                postgresql_mock.is_tls_enabled = PropertyMock(return_value=values[2])
                postgresql_mock.is_restart_pending = PropertyMock(return_value=values[3])

                self.charm._handle_postgresql_restart_need(values[0])
                _reload_patroni_configuration.assert_called_once()
                (
                    self.assertIn(
                        "tls", self.harness.get_relation_data(self.rel_id, self.charm.unit)
                    )
                    if values[0]
                    else self.assertNotIn(
                        "tls", self.harness.get_relation_data(self.rel_id, self.charm.unit)
                    )
                )
                if (values[1] != values[2]) or values[3]:
                    self.assertNotIn(
                        "postgresql_restarted",
                        self.harness.get_relation_data(self.rel_id, self.charm.unit),
                    )
                    _restart.assert_called_once()
                else:
                    (
                        self.assertIn(
                            "postgresql_restarted",
                            self.harness.get_relation_data(self.rel_id, self.charm.unit),
                        )
                        if values[4]
                        else self.assertNotIn(
                            "postgresql_restarted",
                            self.harness.get_relation_data(self.rel_id, self.charm.unit),
                        )
                    )
                    _restart.assert_not_called()

    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch("charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgresqlOperatorCharm._remove_from_members_ips")
    @patch("charm.Patroni.are_all_members_ready")
    @patch("charm.PostgresqlOperatorCharm._get_ips_to_remove")
    @patch("charm.PostgresqlOperatorCharm._updated_synchronous_node_count")
    @patch("charm.Patroni.remove_raft_member")
    @patch("charm.PostgresqlOperatorCharm._unit_ip")
    @patch("charm.Patroni.get_member_ip")
    def test_on_peer_relation_departed(
        self,
        _get_member_ip,
        _unit_ip,
        _remove_raft_member,
        _updated_synchronous_node_count,
        _get_ips_to_remove,
        _are_all_members_ready,
        _remove_from_members_ips,
        _update_config,
        _primary_endpoint,
        _update_relation_endpoints,
    ):
        # Test when the current unit is the departing unit.
        self.charm.unit.status = ActiveStatus()
        event = Mock()
        event.departing_unit = self.harness.charm.unit
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_not_called()
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the current unit is not the departing unit, but removing
        # the member from the raft cluster fails.
        _remove_raft_member.side_effect = RemoveRaftMemberFailedError
        event.departing_unit = Unit(
            f"{self.charm.app.name}/1", None, self.harness.charm.app._backend, {}
        )
        mock_ip_address = "1.1.1.1"
        _get_member_ip.return_value = mock_ip_address
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the member is successfully removed from the raft cluster,
        # but the unit is not the leader.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _remove_raft_member.side_effect = None
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the unit is the leader, but the cluster hasn't initialized yet,
        # or it was unable to set synchronous_node_count.
        _remove_raft_member.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.return_value = False
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id, self.charm.app.name, {"cluster_initialised": "True"}
            )
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with(1)
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when there is more units in the cluster.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        self.harness.add_relation_unit(self.rel_id, f"{self.charm.app.name}/2")
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the cluster is initialised, and it could set synchronous_node_count,
        # but there is no IPs to be removed from the members list.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _updated_synchronous_node_count.return_value = True
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when there are IPs to be removed from the members list, but not all
        # the members are ready yet.
        _remove_raft_member.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _get_ips_to_remove.reset_mock()
        ips_to_remove = ["2.2.2.2", "3.3.3.3"]
        _get_ips_to_remove.return_value = ips_to_remove
        _are_all_members_ready.return_value = False
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when all members are ready.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _get_ips_to_remove.reset_mock()
        _are_all_members_ready.return_value = True
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_has_calls([call(ips_to_remove[0]), call(ips_to_remove[1])])
        self.assertEqual(_update_config.call_count, 2)
        self.assertEqual(_update_relation_endpoints.call_count, 2)
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the primary is not reachable yet.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _get_ips_to_remove.reset_mock()
        _remove_from_members_ips.reset_mock()
        _update_config.reset_mock()
        _update_relation_endpoints.reset_mock()
        _primary_endpoint.return_value = None
        self.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_called_once()
        _update_config.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, WaitingStatus)

    @patch("charm.PostgresqlOperatorCharm._update_relation_endpoints")
    @patch("charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock)
    def test_update_new_unit_status(self, _primary_endpoint, _update_relation_endpoints):
        # Test when the charm is blocked.
        _primary_endpoint.return_value = "endpoint"
        self.charm.unit.status = BlockedStatus("fake blocked status")
        self.charm._update_new_unit_status()
        _update_relation_endpoints.assert_called_once()
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)

        # Test when the charm is not blocked.
        _update_relation_endpoints.reset_mock()
        self.charm.unit.status = WaitingStatus()
        self.charm._update_new_unit_status()
        _update_relation_endpoints.assert_called_once()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the primary endpoint is not reachable yet.
        _update_relation_endpoints.reset_mock()
        _primary_endpoint.return_value = None
        self.charm._update_new_unit_status()
        _update_relation_endpoints.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, WaitingStatus)
