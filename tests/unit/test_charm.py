# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import logging
import platform
import subprocess
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


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    harness.add_relation("upgrade", harness.charm.app.name)
    harness.add_relation(PEER, harness.charm.app.name)
    yield harness
    harness.cleanup()


@patch_network_get(private_address="1.1.1.1")
def test_on_install(harness):
    with patch("charm.subprocess.check_call") as _check_call, patch(
        "charm.snap.SnapCache"
    ) as _snap_cache, patch(
        "charm.PostgresqlOperatorCharm._install_snap_packages"
    ) as _install_snap_packages, patch(
        "charm.PostgresqlOperatorCharm._reboot_on_detached_storage"
    ) as _reboot_on_detached_storage, patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        side_effect=[False, True, True],
    ) as _is_storage_attached:
        # Test without storage.
        harness.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()
        pg_snap = _snap_cache.return_value[POSTGRESQL_SNAP_NAME]

        # Test without adding Patroni resource.
        harness.charm.on.install.emit()
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
        assert isinstance(harness.model.unit.status, WaitingStatus)


@patch_network_get(private_address="1.1.1.1")
def test_on_install_failed_to_create_home(harness):
    with patch("charm.subprocess.check_call") as _check_call, patch(
        "charm.snap.SnapCache"
    ) as _snap_cache, patch(
        "charm.PostgresqlOperatorCharm._install_snap_packages"
    ) as _install_snap_packages, patch(
        "charm.PostgresqlOperatorCharm._reboot_on_detached_storage"
    ) as _reboot_on_detached_storage, patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached",
        side_effect=[False, True, True],
    ) as _is_storage_attached, patch("charm.logger.exception") as _logger_exception:
        # Test without storage.
        harness.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()
        pg_snap = _snap_cache.return_value[POSTGRESQL_SNAP_NAME]
        _check_call.side_effect = [subprocess.CalledProcessError(-1, ["test"])]

        # Test without adding Patroni resource.
        harness.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once_with(packages=SNAP_PACKAGES)
        assert pg_snap.alias.call_count == 2
        pg_snap.alias.assert_any_call("psql")
        pg_snap.alias.assert_any_call("patronictl")

        _logger_exception.assert_called_once_with("Unable to create snap_daemon home dir")

        # Assert the status set by the event handler.
        assert isinstance(harness.model.unit.status, WaitingStatus)


@patch_network_get(private_address="1.1.1.1")
def test_on_install_snap_failure(harness):
    with patch(
        "charm.PostgresqlOperatorCharm._install_snap_packages"
    ) as _install_snap_packages, patch(
        "charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True
    ) as _is_storage_attached:
        # Mock the result of the call.
        _install_snap_packages.side_effect = snap.SnapError
        # Trigger the hook.
        harness.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_packages.assert_called_once()
        assert isinstance(harness.model.unit.status, BlockedStatus)


@patch_network_get(private_address="1.1.1.1")
def test_patroni_scrape_config_no_tls(harness):
    result = harness.charm.patroni_scrape_config()

    assert result == [
        {
            "metrics_path": "/metrics",
            "scheme": "http",
            "static_configs": [{"targets": ["1.1.1.1:8008"]}],
            "tls_config": {"insecure_skip_verify": True},
        },
    ]


@patch_network_get(private_address="1.1.1.1")
def test_patroni_scrape_config_tls(harness):
    with patch(
        "charm.PostgresqlOperatorCharm.is_tls_enabled",
        return_value=True,
        new_callable=PropertyMock,
    ):
        result = harness.charm.patroni_scrape_config()

        assert result == [
            {
                "metrics_path": "/metrics",
                "scheme": "https",
                "static_configs": [{"targets": ["1.1.1.1:8008"]}],
                "tls_config": {"insecure_skip_verify": True},
            },
        ]


def test_primary_endpoint(harness):
    with patch(
        "charm.PostgresqlOperatorCharm._units_ips",
        new_callable=PropertyMock,
        return_value={"1.1.1.1", "1.1.1.2"},
    ), patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock) as _patroni:
        _patroni.return_value.get_member_ip.return_value = "1.1.1.1"
        _patroni.return_value.get_primary.return_value = sentinel.primary
        assert harness.charm.primary_endpoint == "1.1.1.1"

        _patroni.return_value.get_member_ip.assert_called_once_with(sentinel.primary)
        _patroni.return_value.get_primary.assert_called_once_with()


def test_primary_endpoint_no_peers(harness):
    with patch(
        "charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=None
    ), patch(
        "charm.PostgresqlOperatorCharm._units_ips",
        new_callable=PropertyMock,
        return_value={"1.1.1.1", "1.1.1.2"},
    ), patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock) as _patroni:
        assert harness.charm.primary_endpoint is None

        assert not _patroni.return_value.get_member_ip.called
        assert not _patroni.return_value.get_primary.called


@patch_network_get(private_address="1.1.1.1")
def test_on_leader_elected(harness):
    with patch(
        "charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock
    ) as _update_relation_endpoints, patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    ) as _primary_endpoint, patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        # Assert that there is no password in the peer relation.
        assert harness.charm._peers.data[harness.charm.app].get("operator-password", None) is None

        # Check that a new password was generated on leader election.
        _primary_endpoint.return_value = "1.1.1.1"
        harness.set_leader()
        password = harness.charm.get_secret("app", "operator-password")
        _update_config.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        assert password is not None

        # Mark the cluster as initialised.
        harness.charm._peers.data[harness.charm.app].update({"cluster_initialised": "True"})

        # Trigger a new leader election and check that the password is still the same
        # and also that update_endpoints was called after the cluster was initialised.
        harness.set_leader(False)
        harness.set_leader()
        assert harness.charm.get_secret("app", "operator-password") == password
        _update_relation_endpoints.assert_called_once()
        assert not (isinstance(harness.model.unit.status, BlockedStatus))

        # Check for a WaitingStatus when the primary is not reachable yet.
        _primary_endpoint.return_value = None
        harness.set_leader(False)
        harness.set_leader()
        _update_relation_endpoints.assert_called_once()  # Assert it was not called again.
        assert isinstance(harness.model.unit.status, WaitingStatus)


def test_is_cluster_initialised(harness):
    rel_id = harness.model.get_relation(PEER).id
    # Test when the cluster was not initialised yet.
    assert not (harness.charm.is_cluster_initialised)

    # Test when the cluster was already initialised.
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id, harness.charm.app.name, {"cluster_initialised": "True"}
        )
    assert harness.charm.is_cluster_initialised


def test_on_config_changed(harness):
    with patch(
        "charm.PostgresqlOperatorCharm._validate_config_options"
    ) as _validate_config_options, patch(
        "charm.PostgresqlOperatorCharm.update_config"
    ) as _update_config, patch(
        "relations.db.DbProvides.set_up_relation"
    ) as _set_up_relation, patch(
        "charm.PostgresqlOperatorCharm.enable_disable_extensions"
    ) as _enable_disable_extensions, patch(
        "charm.PostgresqlOperatorCharm.is_cluster_initialised", new_callable=PropertyMock
    ) as _is_cluster_initialised:
        # Test when the cluster was not initialised yet.
        _is_cluster_initialised.return_value = False
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_not_called()
        _set_up_relation.assert_not_called()

        # Test when the unit is not the leader.
        _is_cluster_initialised.return_value = True
        harness.charm.on.config_changed.emit()
        _validate_config_options.assert_called_once()
        _enable_disable_extensions.assert_not_called()
        _set_up_relation.assert_not_called()

        # Test unable to connect to db
        _update_config.reset_mock()
        _validate_config_options.side_effect = OperationalError
        harness.charm.on.config_changed.emit()
        assert not _update_config.called
        _validate_config_options.side_effect = None

        # Test after the cluster was initialised.
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_not_called()

        # Test when the unit is in a blocked state due to extensions request,
        # but there are no established legacy relations.
        _enable_disable_extensions.reset_mock()
        harness.charm.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_not_called()

        # Test when the unit is in a blocked state due to extensions request,
        # but there are established legacy relations.
        _enable_disable_extensions.reset_mock()
        _set_up_relation.return_value = False
        db_relation_id = harness.add_relation("db", "application")
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_called_once()
        harness.remove_relation(db_relation_id)

        _enable_disable_extensions.reset_mock()
        _set_up_relation.reset_mock()
        harness.add_relation("db-admin", "application")
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_called_once()

        # Test when  there are established legacy relations,
        # but the charm fails to set up one of them.
        _enable_disable_extensions.reset_mock()
        _set_up_relation.reset_mock()
        _set_up_relation.return_value = False
        harness.add_relation("db", "application")
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()
        _set_up_relation.assert_called_once()


def test_check_extension_dependencies(harness):
    with patch("subprocess.check_output", return_value=b"C"), patch.object(
        PostgresqlOperatorCharm, "postgresql", Mock()
    ):
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
        harness.update_config(config)
        harness.charm.enable_disable_extensions()
        assert not (isinstance(harness.model.unit.status, BlockedStatus))

        # Test when plugins dependencies exception caused
        config["plugin_address_standardizer_enable"] = True
        harness.update_config(config)
        harness.charm.enable_disable_extensions()
        assert isinstance(harness.model.unit.status, BlockedStatus)
        assert harness.model.unit.status.message == EXTENSIONS_DEPENDENCY_MESSAGE


def test_enable_disable_extensions(harness, caplog):
    with patch("subprocess.check_output", return_value=b"C"), patch.object(
        PostgresqlOperatorCharm, "postgresql", Mock()
    ) as postgresql_mock:
        # Test when all extensions install/uninstall succeed.
        postgresql_mock.enable_disable_extension.side_effect = None
        with caplog.at_level(logging.ERROR):
            assert len(caplog.records) == 0
            harness.charm.enable_disable_extensions()
            assert postgresql_mock.enable_disable_extensions.call_count == 1

        # Test when one extension install/uninstall fails.
        postgresql_mock.reset_mock()
        postgresql_mock.enable_disable_extensions.side_effect = (
            PostgreSQLEnableDisableExtensionError
        )
        with caplog.at_level(logging.ERROR):
            harness.charm.enable_disable_extensions()
            assert postgresql_mock.enable_disable_extensions.call_count == 1
            assert "failed to change plugins: " in caplog.text

        # Test when one config option should be skipped (because it's not related
        # to a plugin/extension).
        postgresql_mock.reset_mock()
        postgresql_mock.enable_disable_extensions.side_effect = None
        with caplog.at_level(logging.ERROR):
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
            new_harness = Harness(PostgresqlOperatorCharm, config=config)
            new_harness.cleanup()
            new_harness.begin()
            new_harness.charm.enable_disable_extensions()
            assert postgresql_mock.enable_disable_extensions.call_count == 1


@patch_network_get(private_address="1.1.1.1")
def test_on_start(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.Patroni.get_postgresql_version") as _get_postgresql_version,
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock
        ) as _update_relation_endpoints,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch("charm.PostgreSQLProvider.update_endpoints"),
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch(
            "charm.Patroni.member_started",
            new_callable=PropertyMock,
        ) as _member_started,
        patch("charm.Patroni.bootstrap_cluster") as _bootstrap_cluster,
        patch("charm.PostgresqlOperatorCharm._replication_password") as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch(
            "charm.PostgresqlOperatorCharm._reboot_on_detached_storage"
        ) as _reboot_on_detached_storage,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True) as _idle,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            side_effect=[False, True, True, True, True],
        ) as _is_storage_attached,
    ):
        _get_postgresql_version.return_value = "14.0"

        # Test without storage.
        harness.charm.on.start.emit()
        _reboot_on_detached_storage.assert_called_once()

        # Test before the passwords are generated.
        _member_started.return_value = False
        _get_password.return_value = None
        harness.charm.on.start.emit()
        _bootstrap_cluster.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)

        # Mock the passwords.
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"

        # Mock cluster start and postgres user creation success values.
        _bootstrap_cluster.side_effect = [False, True, True]
        _postgresql.list_users.side_effect = [[], [], []]
        _postgresql.create_user.side_effect = [PostgreSQLCreateUserError, None, None, None]

        # Test for a failed cluster bootstrapping.
        # TODO: test replicas start (DPE-494).
        harness.set_leader()
        harness.charm.on.start.emit()
        _bootstrap_cluster.assert_called_once()
        _oversee_users.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # Set an initial waiting status (like after the install hook was triggered).
        harness.model.unit.status = WaitingStatus("fake message")

        # Test the event of an error happening when trying to create the default postgres user.
        _member_started.return_value = True
        harness.charm.on.start.emit()
        _postgresql.create_user.assert_called_once()
        _oversee_users.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)

        # Set an initial waiting status again (like after the install hook was triggered).
        harness.model.unit.status = WaitingStatus("fake message")

        # Then test the event of a correct cluster bootstrapping.
        harness.charm.on.start.emit()
        assert _postgresql.create_user.call_count == 4  # Considering the previous failed call.
        _oversee_users.assert_called_once()
        _enable_disable_extensions.assert_called_once()
        assert isinstance(harness.model.unit.status, ActiveStatus)


@patch_network_get(private_address="1.1.1.1")
def test_on_start_replica(harness):
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.Patroni.get_postgresql_version") as _get_postgresql_version,
        patch("charm.Patroni.configure_patroni_on_unit") as _configure_patroni_on_unit,
        patch(
            "charm.Patroni.member_started",
            new_callable=PropertyMock,
        ) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock
        ) as _update_relation_endpoints,
        patch.object(EventBase, "defer") as _defer,
        patch("charm.PostgresqlOperatorCharm._replication_password") as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True) as _idle,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            return_value=True,
        ) as _is_storage_attached,
    ):
        _get_postgresql_version.return_value = "14.0"

        # Set the current unit to be a replica (non leader unit).
        harness.set_leader(False)

        # Mock the passwords.
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"

        # Test an uninitialized cluster.
        harness.charm._peers.data[harness.charm.app].update({"cluster_initialised": ""})
        harness.charm.on.start.emit()
        _defer.assert_called_once()

        # Set an initial waiting status again (like after a machine restart).
        harness.model.unit.status = WaitingStatus("fake message")

        # Mark the cluster as initialised and with the workload up and running.
        harness.charm._peers.data[harness.charm.app].update({"cluster_initialised": "True"})
        _member_started.return_value = True
        harness.charm.on.start.emit()
        _configure_patroni_on_unit.assert_not_called()
        assert isinstance(harness.model.unit.status, ActiveStatus)

        # Set an initial waiting status (like after the install hook was triggered).
        harness.model.unit.status = WaitingStatus("fake message")

        # Check that the unit status doesn't change when the workload is not running.
        # In that situation only Patroni is configured in the unit (but not started).
        _member_started.return_value = False
        harness.charm.on.start.emit()
        _configure_patroni_on_unit.assert_called_once()
        assert isinstance(harness.model.unit.status, WaitingStatus)


@patch_network_get(private_address="1.1.1.1")
def test_on_start_no_patroni_member(harness):
    with (
        patch("subprocess.check_output", return_value=b"C"),
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch("charm.Patroni") as patroni,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True) as _idle,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True
        ) as _is_storage_attached,
    ):
        # Mock the passwords.
        patroni.return_value.member_started = False
        _get_password.return_value = "fake-operator-password"
        bootstrap_cluster = patroni.return_value.bootstrap_cluster
        bootstrap_cluster.return_value = True

        patroni.return_value.get_postgresql_version.return_value = "14.0"

        harness.set_leader()
        harness.charm.on.start.emit()
        bootstrap_cluster.assert_called_once()
        _postgresql.create_user.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)
        assert harness.model.unit.status.message == "awaiting for member to start"


def test_on_start_after_blocked_state(harness):
    with (
        patch("charm.Patroni.bootstrap_cluster") as _bootstrap_cluster,
        patch("charm.PostgresqlOperatorCharm._replication_password") as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True
        ) as _is_storage_attached,
    ):
        # Set an initial blocked status (like after the install hook was triggered).
        initial_status = BlockedStatus("fake message")
        harness.model.unit.status = initial_status

        # Test for a failed cluster bootstrapping.
        harness.charm.on.start.emit()
        _get_password.assert_not_called()
        _replication_password.assert_not_called()
        _bootstrap_cluster.assert_not_called()
        # Assert the status didn't change.
        assert harness.model.unit.status == initial_status


@patch_network_get(private_address="1.1.1.1")
def test_on_get_password(harness):
    with patch("charm.PostgresqlOperatorCharm.update_config"):
        rel_id = harness.model.get_relation(PEER).id
        # Create a mock event and set passwords in peer relation data.
        harness.set_leader(True)
        mock_event = MagicMock(params={})
        harness.update_relation_data(
            rel_id,
            harness.charm.app.name,
            {
                "operator-password": "test-password",
                "replication-password": "replication-test-password",
            },
        )

        # Test providing an invalid username.
        mock_event.params["username"] = "user"
        harness.charm._on_get_password(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test without providing the username option.
        mock_event.reset_mock()
        del mock_event.params["username"]
        harness.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "test-password"})

        # Also test providing the username option.
        mock_event.reset_mock()
        mock_event.params["username"] = "replication"
        harness.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "replication-test-password"})


@patch_network_get(private_address="1.1.1.1")
def test_on_set_password(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("charm.PostgresqlOperatorCharm.set_secret") as _set_secret,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Set some values for the other mocks.
        _are_all_members_ready.side_effect = [False, True, True, True, True]
        _postgresql.update_user_password = PropertyMock(
            side_effect=[PostgreSQLUpdateUserPasswordError, None, None, None]
        )

        # Test trying to set a password through a non leader unit.
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test providing an invalid username.
        harness.set_leader()
        mock_event.reset_mock()
        mock_event.params["username"] = "user"
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option but without all cluster members ready.
        mock_event.reset_mock()
        del mock_event.params["username"]
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test for an error updating when updating the user password in the database.
        mock_event.reset_mock()
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option.
        harness.charm._on_set_password(mock_event)
        assert _set_secret.call_args_list[0][0][1] == "operator-password"

        # Also test providing the username option.
        _set_secret.reset_mock()
        mock_event.params["username"] = "replication"
        harness.charm._on_set_password(mock_event)
        assert _set_secret.call_args_list[0][0][1] == "replication-password"

        # And test providing both the username and password options.
        _set_secret.reset_mock()
        mock_event.params["password"] = "replication-test-password"
        harness.charm._on_set_password(mock_event)
        _set_secret.assert_called_once_with(
            "app", "replication-password", "replication-test-password"
        )


@patch_network_get(private_address="1.1.1.1")
def test_on_update_status(harness):
    with (
        patch("charm.ClusterTopologyObserver.start_observer") as _start_observer,
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
        patch("charm.Patroni.restart_patroni") as _restart_patroni,
        patch("charm.Patroni.is_member_isolated") as _is_member_isolated,
        patch("charm.Patroni.reinitialize_postgresql") as _reinitialize_postgresql,
        patch(
            "charm.Patroni.member_replication_lag", new_callable=PropertyMock
        ) as _member_replication_lag,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value=True),
        ) as _primary_endpoint,
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True),
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test before the cluster is initialised.
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()

        # Test after the cluster was initialised, but with the unit in a blocked state.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"cluster_initialised": "True"}
            )
        harness.charm.unit.status = BlockedStatus("fake blocked status")
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()

        # Test with the unit in a status different that blocked.
        harness.charm.unit.status = ActiveStatus()
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_called_once()

        # Test the reinitialisation of the replica when its lag is unknown
        # after a restart.
        _set_primary_status_message.reset_mock()
        _member_started.return_value = False
        _is_member_isolated.return_value = False
        _member_replication_lag.return_value = "unknown"
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.unit.name, {"postgresql_restarted": "True"}
            )
        harness.charm.on.update_status.emit()
        _reinitialize_postgresql.assert_called_once()
        _restart_patroni.assert_not_called()
        _set_primary_status_message.assert_not_called()

        # Test call to restart when the member is isolated from the cluster.
        _is_member_isolated.return_value = True
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.unit.name, {"postgresql_restarted": ""}
            )
        harness.charm.on.update_status.emit()
        _restart_patroni.assert_called_once()
        _start_observer.assert_called_once()


@patch_network_get(private_address="1.1.1.1")
def test_on_update_status_after_restore_operation(harness):
    with (
        patch("charm.ClusterTopologyObserver.start_observer"),
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
        patch(
            "charm.PostgresqlOperatorCharm._handle_workload_failures"
        ) as _handle_workload_failures,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value=True),
        ) as _primary_endpoint,
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch(
            "charm.PostgresqlOperatorCharm._handle_processes_failures"
        ) as _handle_processes_failures,
        patch("charm.PostgreSQLBackups.can_use_s3_repository") as _can_use_s3_repository,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.Patroni.get_member_status") as _get_member_status,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True),
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test when the restore operation fails.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True", "restoring-backup": "2023-01-01T09:00:00Z"},
            )
        _get_member_status.return_value = "failed"
        harness.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _handle_workload_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        assert isinstance(harness.charm.unit.status, BlockedStatus)

        # Test when the restore operation hasn't finished yet.
        harness.charm.unit.status = ActiveStatus()
        _get_member_status.return_value = "running"
        _member_started.return_value = False
        harness.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _handle_workload_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is still in the application relation databag.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True",
            "restoring-backup": "2023-01-01T09:00:00Z",
        }

        # Test when the restore operation finished successfully.
        _member_started.return_value = True
        _can_use_s3_repository.return_value = (True, None)
        _handle_processes_failures.return_value = False
        _handle_workload_failures.return_value = False
        harness.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_called_once()
        _oversee_users.assert_called_once()
        _update_relation_endpoints.assert_called_once()
        _handle_workload_failures.assert_called_once()
        _set_primary_status_message.assert_called_once()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is not in the application relation databag anymore.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True"
        }

        # Test when it's not possible to use the configured S3 repository.
        _update_config.reset_mock()
        _handle_processes_failures.reset_mock()
        _oversee_users.reset_mock()
        _update_relation_endpoints.reset_mock()
        _handle_workload_failures.reset_mock()
        _set_primary_status_message.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        _can_use_s3_repository.return_value = (False, "fake validation message")
        harness.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _handle_workload_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        assert isinstance(harness.charm.unit.status, BlockedStatus)
        assert harness.charm.unit.status.message == "fake validation message"

        # Assert that the backup id is not in the application relation databag anymore.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True"
        }


def test_install_snap_packages(harness):
    with patch("charm.snap.SnapCache") as _snap_cache:
        _snap_package = _snap_cache.return_value.__getitem__.return_value
        _snap_package.ensure.side_effect = snap.SnapError
        _snap_package.present = False

        # Test for problem with snap update.
        with pytest.raises(snap.SnapError):
            harness.charm._install_snap_packages([("postgresql", {"channel": "14/edge"})])
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_cache.assert_called_once_with()
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, channel="14/edge")

        # Test with a not found package.
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = snap.SnapNotFoundError
        with pytest.raises(snap.SnapNotFoundError):
            harness.charm._install_snap_packages([("postgresql", {"channel": "14/edge"})])
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_cache.assert_called_once_with()
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, channel="14/edge")

        # Then test a valid one.
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = None
        harness.charm._install_snap_packages([("postgresql", {"channel": "14/edge"})])
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Latest, channel="14/edge")
        _snap_package.hold.assert_not_called()

        # Test revision
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = None
        harness.charm._install_snap_packages([
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
        harness.charm._install_snap_packages(
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
        harness.charm._install_snap_packages([
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
        with pytest.raises(KeyError):
            harness.charm._install_snap_packages(
                [("postgresql", {"revision": {"missingarch": "42"}})],
                refresh=True,
            )
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("postgresql")
        assert not _snap_package.ensure.called
        assert not _snap_package.hold.called


def test_is_storage_attached(harness):
    with patch(
        "subprocess.check_call",
        side_effect=[None, subprocess.CalledProcessError(1, "fake command")],
    ) as _check_call:
        # Test with attached storage.
        is_storage_attached = harness.charm._is_storage_attached()
        _check_call.assert_called_once_with(["mountpoint", "-q", harness.charm._storage_path])
        assert is_storage_attached

        # Test with detached storage.
        is_storage_attached = harness.charm._is_storage_attached()
        assert not (is_storage_attached)


def test_reboot_on_detached_storage(harness):
    with patch("subprocess.check_call") as _check_call:
        mock_event = MagicMock()
        harness.charm._reboot_on_detached_storage(mock_event)
        mock_event.defer.assert_called_once()
        assert isinstance(harness.charm.unit.status, WaitingStatus)
        _check_call.assert_called_once_with(["systemctl", "reboot"])


@patch_network_get(private_address="1.1.1.1")
def test_restart(harness):
    with (
        patch("charm.Patroni.restart_postgresql") as _restart_postgresql,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
    ):
        _are_all_members_ready.side_effect = [False, True, True]

        # Test when not all members are ready.
        mock_event = MagicMock()
        harness.charm._restart(mock_event)
        mock_event.defer.assert_called_once()
        _restart_postgresql.assert_not_called()

        # Test a successful restart.
        mock_event.defer.reset_mock()
        harness.charm._restart(mock_event)
        assert not (isinstance(harness.charm.unit.status, BlockedStatus))
        mock_event.defer.assert_not_called()

        # Test a failed restart.
        _restart_postgresql.side_effect = RetryError(last_attempt=1)
        harness.charm._restart(mock_event)
        assert isinstance(harness.charm.unit.status, BlockedStatus)
        mock_event.defer.assert_not_called()


@patch_network_get(private_address="1.1.1.1")
def test_update_config(harness):
    with (
        patch("subprocess.check_output", return_value=b"C"),
        patch("charm.snap.SnapCache"),
        patch(
            "charm.PostgresqlOperatorCharm._handle_postgresql_restart_need"
        ) as _handle_postgresql_restart_need,
        patch("charm.Patroni.bulk_update_parameters_controller_by_patroni"),
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm._is_workload_running", new_callable=PropertyMock
        ) as _is_workload_running,
        patch("charm.Patroni.render_patroni_yml_file") as _render_patroni_yml_file,
        patch(
            "charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock
        ) as _is_tls_enabled,
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Mock some properties.
        postgresql_mock.is_tls_enabled = PropertyMock(side_effect=[False, False, False, False])
        _is_workload_running.side_effect = [False, False, True, True, False, True]
        _member_started.side_effect = [True, True, False]
        postgresql_mock.build_postgresql_parameters.return_value = {"test": "test"}

        # Test when only one of the two config options for profile limit memory is set.
        harness.update_config({"profile-limit-memory": 1000})
        harness.charm.update_config()

        # Test when only one of the two config options for profile limit memory is set.
        harness.update_config({"profile_limit_memory": 1000}, unset={"profile-limit-memory"})
        harness.charm.update_config()

        # Test when the two config options for profile limit memory are set at the same time.
        _render_patroni_yml_file.reset_mock()
        harness.update_config({"profile-limit-memory": 1000})
        with pytest.raises(ValueError):
            harness.charm.update_config()

        # Test without TLS files available.
        harness.update_config(unset={"profile-limit-memory", "profile_limit_memory"})
        with harness.hooks_disabled():
            harness.update_relation_data(rel_id, harness.charm.unit.name, {"tls": ""})
        _is_tls_enabled.return_value = False
        harness.charm.update_config()
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
        assert "tls" not in harness.get_relation_data(rel_id, harness.charm.unit.name)

        # Test with TLS files available.
        _handle_postgresql_restart_need.reset_mock()
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it change.
        _is_tls_enabled.return_value = True
        _render_patroni_yml_file.reset_mock()
        harness.charm.update_config()
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
        assert "tls" not in harness.get_relation_data(
            rel_id, harness.charm.unit.name
        )  # The "tls" flag is set in handle_postgresql_restart_need.

        # Test with workload not running yet.
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it change.
        _handle_postgresql_restart_need.reset_mock()
        harness.charm.update_config()
        _handle_postgresql_restart_need.assert_not_called()
        assert harness.get_relation_data(rel_id, harness.charm.unit.name)["tls"] == "enabled"

        # Test with member not started yet.
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it doesn't change.
        harness.charm.update_config()
        _handle_postgresql_restart_need.assert_not_called()
        assert "tls" not in harness.get_relation_data(rel_id, harness.charm.unit.name)


def test_on_cluster_topology_change(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
    ):
        # Mock the property value.
        _primary_endpoint.side_effect = [None, "1.1.1.1"]

        # Test without an elected primary.
        harness.charm._on_cluster_topology_change(Mock())
        _update_relation_endpoints.assert_not_called()

        # Test with an elected primary.
        harness.charm._on_cluster_topology_change(Mock())
        _update_relation_endpoints.assert_called_once()


def test_on_cluster_topology_change_keep_blocked(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value=None,
        ) as _primary_endpoint,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
    ):
        harness.model.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

        harness.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_not_called()
        _primary_endpoint.assert_called_once_with()
        assert isinstance(harness.model.unit.status, WaitingStatus)
        assert harness.model.unit.status.message == PRIMARY_NOT_REACHABLE_MESSAGE


def test_on_cluster_topology_change_clear_blocked(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value="fake-unit",
        ) as _primary_endpoint,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
    ):
        harness.model.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

        harness.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_called_once_with()
        _primary_endpoint.assert_called_once_with()
        assert isinstance(harness.model.unit.status, ActiveStatus)


def test_validate_config_options(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.postgresql", new_callable=PropertyMock) as _charm_lib,
        patch("config.subprocess"),
    ):
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = []
        _charm_lib.return_value.validate_date_style.return_value = []
        _charm_lib.return_value.get_postgresql_timezones.return_value = []

        # Test instance_default_text_search_config exception
        with harness.hooks_disabled():
            harness.update_config({"instance_default_text_search_config": "pg_catalog.test"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
            assert (
                e.msg == "instance_default_text_search_config config option has an invalid value"
            )

        _charm_lib.return_value.get_postgresql_text_search_configs.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = [
            "pg_catalog.test"
        ]

        # Test request_date_style exception
        with harness.hooks_disabled():
            harness.update_config({"request_date_style": "ISO, TEST"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
            assert e.msg == "request_date_style config option has an invalid value"

        _charm_lib.return_value.validate_date_style.assert_called_once_with("ISO, TEST")
        _charm_lib.return_value.validate_date_style.return_value = ["ISO, TEST"]

        # Test request_time_zone exception
        with harness.hooks_disabled():
            harness.update_config({"request_time_zone": "TEST_ZONE"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
            assert e.msg == "request_time_zone config option has an invalid value"

        _charm_lib.return_value.get_postgresql_timezones.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_timezones.return_value = ["TEST_ZONE"]


@patch_network_get(private_address="1.1.1.1")
def test_on_peer_relation_changed(harness):
    with (
        patch("charm.snap.SnapCache"),
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
        patch("backups.PostgreSQLBackups.check_stanza") as _check_stanza,
        patch("backups.PostgreSQLBackups.coordinate_stanza_fields") as _coordinate_stanza_fields,
        patch(
            "backups.PostgreSQLBackups.start_stop_pgbackrest_service"
        ) as _start_stop_pgbackrest_service,
        patch("charm.Patroni.reinitialize_postgresql") as _reinitialize_postgresql,
        patch(
            "charm.Patroni.member_replication_lag", new_callable=PropertyMock
        ) as _member_replication_lag,
        patch("charm.PostgresqlOperatorCharm.is_primary") as _is_primary,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.Patroni.start_patroni") as _start_patroni,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._update_member_ip") as _update_member_ip,
        patch("charm.PostgresqlOperatorCharm._reconfigure_cluster") as _reconfigure_cluster,
        patch("ops.framework.EventBase.defer") as _defer,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test an uninitialized cluster.
        mock_event = Mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"cluster_initialised": ""}
            )
        harness.charm._on_peer_relation_changed(mock_event)
        mock_event.defer.assert_called_once()
        _reconfigure_cluster.assert_not_called()

        # Test an initialized cluster and this is the leader unit
        # (but it fails to reconfigure the cluster).
        mock_event.defer.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True", "members_ips": '["1.1.1.1"]'},
            )
            harness.set_leader()
        _reconfigure_cluster.return_value = False
        harness.charm._on_peer_relation_changed(mock_event)
        _reconfigure_cluster.assert_called_once_with(mock_event)
        mock_event.defer.assert_called_once()

        # Test when the leader can reconfigure the cluster.
        mock_event.defer.reset_mock()
        _reconfigure_cluster.reset_mock()
        _reconfigure_cluster.return_value = True
        _update_member_ip.return_value = False
        _member_started.return_value = True
        _primary_endpoint.return_value = "1.1.1.1"
        harness.model.unit.status = WaitingStatus("awaiting for cluster to start")
        harness.charm._on_peer_relation_changed(mock_event)
        mock_event.defer.assert_not_called()
        _reconfigure_cluster.assert_called_once_with(mock_event)
        _update_member_ip.assert_called_once()
        _update_config.assert_called_once()
        _start_patroni.assert_called_once()
        _update_relation_endpoints.assert_called_once()
        assert isinstance(harness.model.unit.status, ActiveStatus)

        # Test when the cluster member updates its IP.
        _update_member_ip.reset_mock()
        _update_config.reset_mock()
        _start_patroni.reset_mock()
        _update_relation_endpoints.reset_mock()
        _update_member_ip.return_value = True
        harness.charm._on_peer_relation_changed(mock_event)
        _update_member_ip.assert_called_once()
        _update_config.assert_not_called()
        _start_patroni.assert_not_called()
        _update_relation_endpoints.assert_not_called()

        # Test when the unit fails to update the Patroni configuration.
        _update_member_ip.return_value = False
        _update_config.side_effect = RetryError(last_attempt=1)
        harness.charm._on_peer_relation_changed(mock_event)
        _update_config.assert_called_once()
        _start_patroni.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)

        # Test when Patroni hasn't started yet in the unit.
        _update_config.side_effect = None
        _member_started.return_value = False
        harness.charm._on_peer_relation_changed(mock_event)
        _start_patroni.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)

        # Test when Patroni has already started but this is a replica with a
        # huge or unknown lag.
        relation = harness.model.get_relation(PEER, rel_id)
        _member_started.return_value = True
        for values in itertools.product([True, False], ["0", "1000", "1001", "unknown"]):
            _defer.reset_mock()
            _check_stanza.reset_mock()
            _start_stop_pgbackrest_service.reset_mock()
            _is_primary.return_value = values[0]
            _member_replication_lag.return_value = values[1]
            harness.charm.unit.status = ActiveStatus()
            harness.charm.on.database_peers_relation_changed.emit(relation)
            if _is_primary.return_value == values[0] or int(values[1]) <= 1000:
                _defer.assert_not_called()
                _check_stanza.assert_called_once()
                _start_stop_pgbackrest_service.assert_called_once()
                assert isinstance(harness.charm.unit.status, ActiveStatus)
            else:
                _defer.assert_called_once()
                _check_stanza.assert_not_called()
                _start_stop_pgbackrest_service.assert_not_called()
                assert isinstance(harness.charm.unit.status, MaintenanceStatus)

        # Test when it was not possible to start the pgBackRest service yet.
        relation = harness.model.get_relation(PEER, rel_id)
        _member_started.return_value = True
        _defer.reset_mock()
        _coordinate_stanza_fields.reset_mock()
        _check_stanza.reset_mock()
        _start_stop_pgbackrest_service.return_value = False
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_called_once()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()

        # Test the last calls been made when it was possible to start the
        # pgBackRest service.
        _defer.reset_mock()
        _start_stop_pgbackrest_service.return_value = True
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_not_called()
        _coordinate_stanza_fields.assert_called_once()
        _check_stanza.assert_called_once()


@patch_network_get(private_address="1.1.1.1")
def test_reconfigure_cluster(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._add_members") as _add_members,
        patch(
            "charm.PostgresqlOperatorCharm._remove_from_members_ips"
        ) as _remove_from_members_ips,
        patch("charm.Patroni.remove_raft_member") as _remove_raft_member,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test when no change is needed in the member IP.
        mock_event = Mock()
        mock_event.unit = harness.charm.unit
        mock_event.relation.data = {mock_event.unit: {}}
        assert harness.charm._reconfigure_cluster(mock_event)
        _remove_raft_member.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_called_once_with(mock_event)

        # Test when a change is needed in the member IP, but it fails.
        _remove_raft_member.side_effect = RemoveRaftMemberFailedError
        _add_members.reset_mock()
        ip_to_remove = "1.1.1.1"
        relation_data = {mock_event.unit: {"ip-to-remove": ip_to_remove}}
        mock_event.relation.data = relation_data
        assert not (harness.charm._reconfigure_cluster(mock_event))
        _remove_raft_member.assert_called_once_with(ip_to_remove)
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_not_called()

        # Test when a change is needed in the member IP, and it succeeds
        # (but the old IP was already been removed).
        _remove_raft_member.reset_mock()
        _remove_raft_member.side_effect = None
        _add_members.reset_mock()
        mock_event.relation.data = relation_data
        assert harness.charm._reconfigure_cluster(mock_event)
        _remove_raft_member.assert_called_once_with(ip_to_remove)
        _remove_from_members_ips.assert_not_called()
        _add_members.assert_called_once_with(mock_event)

        # Test when the old IP wasn't removed yet.
        _remove_raft_member.reset_mock()
        _add_members.reset_mock()
        mock_event.relation.data = relation_data
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"members_ips": '["' + ip_to_remove + '"]'}
            )
        assert harness.charm._reconfigure_cluster(mock_event)
        _remove_raft_member.assert_called_once_with(ip_to_remove)
        _remove_from_members_ips.assert_called_once_with(ip_to_remove)
        _add_members.assert_called_once_with(mock_event)


def test_update_certificate(harness):
    with (
        patch(
            "charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate"
        ) as _request_certificate,
    ):
        # If there is no current TLS files, _request_certificate should be called
        # only when the certificates relation is established.
        harness.charm._update_certificate()
        _request_certificate.assert_not_called()

        # Test with already present TLS files (when they will be replaced by new ones).
        ca = "fake CA"
        cert = "fake certificate"
        key = private_key = "fake private key"
        harness.charm.set_secret("unit", "ca", ca)
        harness.charm.set_secret("unit", "cert", cert)
        harness.charm.set_secret("unit", "key", key)
        harness.charm.set_secret("unit", "private-key", private_key)

        harness.charm._update_certificate()
        _request_certificate.assert_called_once_with(private_key)

        assert harness.charm.get_secret("unit", "ca") == ca
        assert harness.charm.get_secret("unit", "cert") == cert
        assert harness.charm.get_secret("unit", "key") == key
        assert harness.charm.get_secret("unit", "private-key") == private_key


@patch_network_get(private_address="1.1.1.1")
def test_update_member_ip(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._update_certificate") as _update_certificate,
        patch("charm.Patroni.stop_patroni") as _stop_patroni,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test when the IP address of the unit hasn't changed.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.unit.name,
                {
                    "ip": "1.1.1.1",
                },
            )
        assert not (harness.charm._update_member_ip())
        relation_data = harness.get_relation_data(rel_id, harness.charm.unit.name)
        assert relation_data.get("ip-to-remove") is None
        _stop_patroni.assert_not_called()
        _update_certificate.assert_not_called()

        # Test when the IP address of the unit has changed.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.unit.name,
                {
                    "ip": "2.2.2.2",
                },
            )
        assert harness.charm._update_member_ip()
        relation_data = harness.get_relation_data(rel_id, harness.charm.unit.name)
        assert relation_data.get("ip") == "1.1.1.1"
        assert relation_data.get("ip-to-remove") == "2.2.2.2"
        _stop_patroni.assert_called_once()
        _update_certificate.assert_called_once()


@patch_network_get(private_address="1.1.1.1")
def test_push_tls_files_to_workload(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.render_file") as _render_file,
        patch(
            "charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS.get_tls_files"
        ) as _get_tls_files,
    ):
        _get_tls_files.side_effect = [
            ("key", "ca", "cert"),
            ("key", "ca", None),
            ("key", None, "cert"),
            (None, "ca", "cert"),
        ]
        _update_config.side_effect = [True, False, False, False]

        # Test when all TLS files are available.
        assert harness.charm.push_tls_files_to_workload()
        assert _render_file.call_count == 3

        # Test when not all TLS files are available.
        for _ in range(3):
            _render_file.reset_mock()
            assert not (harness.charm.push_tls_files_to_workload())
            assert _render_file.call_count == 2


def test_is_workload_running(harness):
    with patch("charm.snap.SnapCache") as _snap_cache:
        pg_snap = _snap_cache.return_value[POSTGRESQL_SNAP_NAME]

        pg_snap.present = False
        assert not (harness.charm._is_workload_running)

        pg_snap.present = True
        assert harness.charm._is_workload_running


def test_get_available_memory(harness):
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
        assert harness.charm.get_available_memory() == 16475635712

    with patch("builtins.open", mock_open(read_data="")):
        assert harness.charm.get_available_memory() == 0


def test_juju_run_exec_divergence(harness):
    with (
        patch("charm.ClusterTopologyObserver") as _topology_observer,
        patch("charm.JujuVersion") as _juju_version,
    ):
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


def test_client_relations(harness):
    # Test when the charm has no relations.
    assert len(harness.charm.client_relations) == 0

    # Test when the charm has some relations.
    harness.add_relation("database", "application")
    harness.add_relation("db", "legacy-application")
    harness.add_relation("db-admin", "legacy-admin-application")
    database_relation = harness.model.get_relation("database")
    db_relation = harness.model.get_relation("db")
    db_admin_relation = harness.model.get_relation("db-admin")
    assert harness.charm.client_relations == [database_relation, db_relation, db_admin_relation]


#
# Secrets
#


def test_scope_obj(harness):
    assert harness.charm._scope_obj("app") == harness.charm.framework.model.app
    assert harness.charm._scope_obj("unit") == harness.charm.framework.model.unit
    assert harness.charm._scope_obj("test") is None


@patch_network_get(private_address="1.1.1.1")
def test_get_secret(harness):
    with patch("charm.PostgresqlOperatorCharm._on_leader_elected"):
        rel_id = harness.model.get_relation(PEER).id
        # App level changes require leader privileges
        harness.set_leader()
        # Test application scope.
        assert harness.charm.get_secret("app", "password") is None
        harness.update_relation_data(rel_id, harness.charm.app.name, {"password": "test-password"})
        assert harness.charm.get_secret("app", "password") == "test-password"

        # Unit level changes don't require leader privileges
        harness.set_leader(False)
        # Test unit scope.
        assert harness.charm.get_secret("unit", "password") is None
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"password": "test-password"}
        )
        assert harness.charm.get_secret("unit", "password") == "test-password"


@patch_network_get(private_address="1.1.1.1")
def test_on_get_password_secrets(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # Create a mock event and set passwords in peer relation data.
        harness.set_leader()
        mock_event = MagicMock(params={})
        harness.charm.set_secret("app", "operator-password", "test-password")
        harness.charm.set_secret("app", "replication-password", "replication-test-password")

        # Test providing an invalid username.
        mock_event.params["username"] = "user"
        harness.charm._on_get_password(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test without providing the username option.
        mock_event.reset_mock()
        del mock_event.params["username"]
        harness.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "test-password"})

        # Also test providing the username option.
        mock_event.reset_mock()
        mock_event.params["username"] = "replication"
        harness.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "replication-test-password"})


@pytest.mark.parametrize("scope", [("app"), ("unit")])
@patch_network_get(private_address="1.1.1.1")
def test_get_secret_secrets(harness, scope):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        harness.set_leader()

        assert harness.charm.get_secret(scope, "operator-password") is None
        harness.charm.set_secret(scope, "operator-password", "test-password")
        assert harness.charm.get_secret(scope, "operator-password") == "test-password"


@patch_network_get(private_address="1.1.1.1")
def test_set_secret(harness):
    with patch("charm.PostgresqlOperatorCharm._on_leader_elected"):
        rel_id = harness.model.get_relation(PEER).id
        harness.set_leader()

        # Test application scope.
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.app.name)
        harness.charm.set_secret("app", "password", "test-password")
        assert (
            harness.get_relation_data(rel_id, harness.charm.app.name)["password"]
            == "test-password"
        )
        harness.charm.set_secret("app", "password", None)
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.app.name)

        # Test unit scope.
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.unit.name)
        harness.charm.set_secret("unit", "password", "test-password")
        assert (
            harness.get_relation_data(rel_id, harness.charm.unit.name)["password"]
            == "test-password"
        )
        harness.charm.set_secret("unit", "password", None)
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.unit.name)

        with pytest.raises(RuntimeError):
            harness.charm.set_secret("test", "password", "test")


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_set_reset_new_secret(harness, scope, is_leader):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        """NOTE: currently ops.testing seems to allow for non-leader to set secrets too!"""
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)
        # Getting current password
        harness.charm.set_secret(scope, "new-secret", "bla")
        assert harness.charm.get_secret(scope, "new-secret") == "bla"

        # Reset new secret
        harness.charm.set_secret(scope, "new-secret", "blablabla")
        assert harness.charm.get_secret(scope, "new-secret") == "blablabla"

        # Set another new secret
        harness.charm.set_secret(scope, "new-secret2", "blablabla")
        assert harness.charm.get_secret(scope, "new-secret2") == "blablabla"


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_invalid_secret(harness, scope, is_leader):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        with pytest.raises(RelationDataTypeError):
            harness.charm.set_secret(scope, "somekey", 1)

        harness.charm.set_secret(scope, "somekey", "")
        assert harness.charm.get_secret(scope, "somekey") is None


@patch_network_get(private_address="1.1.1.1")
def test_delete_password(harness, _has_secrets, caplog):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        """NOTE: currently ops.testing seems to allow for non-leader to remove secrets too!"""
        harness.set_leader(True)
        harness.charm.set_secret("app", "operator-password", "somepw")
        harness.charm.remove_secret("app", "operator-password")
        assert harness.charm.get_secret("app", "operator-password") is None

        harness.set_leader(False)
        harness.charm.set_secret("unit", "operator-password", "somesecret")
        harness.charm.remove_secret("unit", "operator-password")
        assert harness.charm.get_secret("unit", "operator-password") is None

        harness.set_leader(True)
        with caplog.at_level(logging.ERROR):
            if _has_secrets:
                error_message = (
                    "Non-existing secret operator-password was attempted to be removed."
                )
            else:
                error_message = (
                    "Non-existing field 'operator-password' was attempted to be removed"
                )

            harness.charm.remove_secret("app", "operator-password")
            assert error_message in caplog.text

            harness.charm.remove_secret("unit", "operator-password")
            assert error_message in caplog.text

            harness.charm.remove_secret("app", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in caplog.text
            )

            harness.charm.remove_secret("unit", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in caplog.text
            )


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_migration_from_databag(harness, _has_secrets, scope, is_leader):
    """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage."""
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # as this test checks for a migration from databag to secrets,
        # there's no need for this test when secrets are not enabled.
        if not _has_secrets:
            return

        rel_id = harness.model.get_relation(PEER).id
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        # Getting current password
        entity = getattr(harness.charm, scope)
        harness.update_relation_data(rel_id, entity.name, {"operator-password": "bla"})
        assert harness.charm.get_secret(scope, "operator-password") == "bla"

        # Reset new secret
        harness.charm.set_secret(scope, "operator-password", "blablabla")
        assert harness.charm.model.get_secret(label=f"postgresql.{scope}")
        assert harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert "operator-password" not in harness.get_relation_data(
            rel_id, getattr(harness.charm, scope).name
        )


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_migration_from_single_secret(harness, _has_secrets, scope, is_leader):
    """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage."""
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # as this test checks for a migration from databag to secrets,
        # there's no need for this test when secrets are not enabled.
        if not _has_secrets:
            return

        rel_id = harness.model.get_relation(PEER).id

        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        secret = harness.charm.app.add_secret({"operator-password": "bla"})

        # Getting current password
        entity = getattr(harness.charm, scope)
        harness.update_relation_data(rel_id, entity.name, {SECRET_INTERNAL_LABEL: secret.id})
        assert harness.charm.get_secret(scope, "operator-password") == "bla"

        # Reset new secret
        # Only the leader can set app secret content.
        with harness.hooks_disabled():
            harness.set_leader(True)
        harness.charm.set_secret(scope, "operator-password", "blablabla")
        with harness.hooks_disabled():
            harness.set_leader(is_leader)
        assert harness.charm.model.get_secret(label=f"postgresql.{scope}")
        assert harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert SECRET_INTERNAL_LABEL not in harness.get_relation_data(
            rel_id, getattr(harness.charm, scope).name
        )


def test_handle_postgresql_restart_need(harness):
    with (
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock") as _restart,
        patch("charm.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch("charm.PostgresqlOperatorCharm._unit_ip"),
        patch(
            "charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock
        ) as _is_tls_enabled,
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
    ):
        rel_id = harness.model.get_relation(PEER).id
        for values in itertools.product(
            [True, False], [True, False], [True, False], [True, False], [True, False]
        ):
            _reload_patroni_configuration.reset_mock()
            _restart.reset_mock()
            with harness.hooks_disabled():
                harness.update_relation_data(rel_id, harness.charm.unit.name, {"tls": ""})
                harness.update_relation_data(
                    rel_id,
                    harness.charm.unit.name,
                    {"postgresql_restarted": ("True" if values[4] else "")},
                )

            _is_tls_enabled.return_value = values[1]
            postgresql_mock.is_tls_enabled = PropertyMock(return_value=values[2])
            postgresql_mock.is_restart_pending = PropertyMock(return_value=values[3])

            harness.charm._handle_postgresql_restart_need(values[0])
            _reload_patroni_configuration.assert_called_once()
            if values[0]:
                assert "tls" in harness.get_relation_data(rel_id, harness.charm.unit)
            else:
                assert "tls" not in harness.get_relation_data(rel_id, harness.charm.unit)

            if (values[1] != values[2]) or values[3]:
                assert "postgresql_restarted" not in harness.get_relation_data(
                    rel_id, harness.charm.unit
                )
                _restart.assert_called_once()
            else:
                if values[4]:
                    assert "postgresql_restarted" in harness.get_relation_data(
                        rel_id, harness.charm.unit
                    )
                else:
                    assert "postgresql_restarted" not in harness.get_relation_data(
                        rel_id, harness.charm.unit
                    )
                _restart.assert_not_called()


def test_on_peer_relation_departed(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch(
            "charm.PostgresqlOperatorCharm._remove_from_members_ips"
        ) as _remove_from_members_ips,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
        patch("charm.PostgresqlOperatorCharm._get_ips_to_remove") as _get_ips_to_remove,
        patch(
            "charm.PostgresqlOperatorCharm._updated_synchronous_node_count"
        ) as _updated_synchronous_node_count,
        patch("charm.Patroni.remove_raft_member") as _remove_raft_member,
        patch("charm.PostgresqlOperatorCharm._unit_ip") as _unit_ip,
        patch("charm.Patroni.get_member_ip") as _get_member_ip,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test when the current unit is the departing unit.
        harness.charm.unit.status = ActiveStatus()
        event = Mock()
        event.departing_unit = harness.charm.unit
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_not_called()
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the current unit is not the departing unit, but removing
        # the member from the raft cluster fails.
        _remove_raft_member.side_effect = RemoveRaftMemberFailedError
        event.departing_unit = Unit(
            f"{harness.charm.app.name}/1", None, harness.charm.app._backend, {}
        )
        mock_ip_address = "1.1.1.1"
        _get_member_ip.return_value = mock_ip_address
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the member is successfully removed from the raft cluster,
        # but the unit is not the leader.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _remove_raft_member.side_effect = None
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the unit is the leader, but the cluster hasn't initialized yet,
        # or it was unable to set synchronous_node_count.
        _remove_raft_member.reset_mock()
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_not_called()
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.return_value = False
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"cluster_initialised": "True"}
            )
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with(1)
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when there is more units in the cluster.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        harness.add_relation_unit(rel_id, f"{harness.charm.app.name}/2")
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_not_called()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the cluster is initialised, and it could set synchronous_node_count,
        # but there is no IPs to be removed from the members list.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _updated_synchronous_node_count.return_value = True
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when there are IPs to be removed from the members list, but not all
        # the members are ready yet.
        _remove_raft_member.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _get_ips_to_remove.reset_mock()
        ips_to_remove = ["2.2.2.2", "3.3.3.3"]
        _get_ips_to_remove.return_value = ips_to_remove
        _are_all_members_ready.return_value = False
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_not_called()
        _update_config.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when all members are ready.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _get_ips_to_remove.reset_mock()
        _are_all_members_ready.return_value = True
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_has_calls([call(ips_to_remove[0]), call(ips_to_remove[1])])
        assert _update_config.call_count == 2
        assert _update_relation_endpoints.call_count == 2
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the primary is not reachable yet.
        _remove_raft_member.reset_mock()
        event.defer.reset_mock()
        _updated_synchronous_node_count.reset_mock()
        _get_ips_to_remove.reset_mock()
        _remove_from_members_ips.reset_mock()
        _update_config.reset_mock()
        _update_relation_endpoints.reset_mock()
        _primary_endpoint.return_value = None
        harness.charm._on_peer_relation_departed(event)
        _remove_raft_member.assert_called_once_with(mock_ip_address)
        event.defer.assert_not_called()
        _updated_synchronous_node_count.assert_called_once_with(2)
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_called_once()
        _update_config.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, WaitingStatus)


def test_update_new_unit_status(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
    ):
        # Test when the charm is blocked.
        _primary_endpoint.return_value = "endpoint"
        harness.charm.unit.status = BlockedStatus("fake blocked status")
        harness.charm._update_new_unit_status()
        _update_relation_endpoints.assert_called_once()
        assert isinstance(harness.charm.unit.status, BlockedStatus)

        # Test when the charm is not blocked.
        _update_relation_endpoints.reset_mock()
        harness.charm.unit.status = WaitingStatus()
        harness.charm._update_new_unit_status()
        _update_relation_endpoints.assert_called_once()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the primary endpoint is not reachable yet.
        _update_relation_endpoints.reset_mock()
        _primary_endpoint.return_value = None
        harness.charm._update_new_unit_status()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, WaitingStatus)
