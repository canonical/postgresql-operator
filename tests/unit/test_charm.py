# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import json
import logging
import os
import pathlib
import platform
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, PropertyMock, call, mock_open, patch, sentinel

import charm_refresh
import psycopg2
import pytest
import tomli
from charms.operator_libs_linux.v2 import snap
from charms.postgresql_k8s.v1.postgresql import (
    PostgreSQLCreateUserError,
    PostgreSQLEnableDisableExtensionError,
)
from ops import Unit
from ops.framework import EventBase
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    RelationDataTypeError,
    WaitingStatus,
)
from ops.testing import Harness
from psycopg2 import OperationalError
from tenacity import RetryError, wait_fixed

from backups import CANNOT_RESTORE_PITR
from charm import (
    EXTENSIONS_DEPENDENCY_MESSAGE,
    PRIMARY_NOT_REACHABLE_MESSAGE,
    PostgresqlOperatorCharm,
)
from cluster import (
    NotReadyError,
    RemoveRaftMemberFailedError,
    SwitchoverFailedError,
    SwitchoverNotSyncError,
)
from constants import PEER, POSTGRESQL_DATA_PATH, SECRET_INTERNAL_LABEL, UPDATE_CERTS_BIN_PATH

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"

# used for assert functions


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    harness.add_relation(PEER, harness.charm.app.name)
    harness.add_relation("restart", harness.charm.app.name)
    yield harness
    harness.cleanup()


def test_on_install(harness):
    with (
        patch("charm.subprocess.check_call") as _check_call,
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.PostgresqlOperatorCharm._install_snap_package") as _install_snap_package,
        patch(
            "charm.PostgresqlOperatorCharm._reboot_on_detached_storage"
        ) as _reboot_on_detached_storage,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            side_effect=[False, True, True],
        ) as _is_storage_attached,
    ):
        # Test without storage.
        harness.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()
        pg_snap = _snap_cache.return_value[charm_refresh.snap_name()]

        # Test without adding Patroni resource.
        harness.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_package.assert_called_once_with(revision=None)
        assert pg_snap.alias.call_count == 2
        pg_snap.alias.assert_any_call("psql")
        pg_snap.alias.assert_any_call("patronictl")

        assert _check_call.call_count == 3
        _check_call.assert_any_call(["mkdir", "-p", "/home/snap_daemon"])
        _check_call.assert_any_call(["chown", "snap_daemon:snap_daemon", "/home/snap_daemon"])
        _check_call.assert_any_call(["usermod", "-d", "/home/snap_daemon", "snap_daemon"])

        # Assert the status set by the event handler.
        assert isinstance(harness.model.unit.status, WaitingStatus)


def test_on_install_failed_to_create_home(harness):
    with (
        patch("charm.subprocess.check_call") as _check_call,
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.PostgresqlOperatorCharm._install_snap_package") as _install_snap_package,
        patch(
            "charm.PostgresqlOperatorCharm._reboot_on_detached_storage"
        ) as _reboot_on_detached_storage,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            side_effect=[False, True, True],
        ) as _is_storage_attached,
        patch("charm.logger.exception") as _logger_exception,
    ):
        # Test without storage.
        harness.charm.on.install.emit()
        _reboot_on_detached_storage.assert_called_once()
        pg_snap = _snap_cache.return_value[charm_refresh.snap_name()]
        _check_call.side_effect = [subprocess.CalledProcessError(-1, ["test"])]

        # Test without adding Patroni resource.
        harness.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_package.assert_called_once_with(revision=None)
        assert pg_snap.alias.call_count == 2
        pg_snap.alias.assert_any_call("psql")
        pg_snap.alias.assert_any_call("patronictl")

        _logger_exception.assert_called_once_with("Unable to create snap_daemon home dir")

        # Assert the status set by the event handler.
        assert isinstance(harness.model.unit.status, WaitingStatus)


def test_on_install_snap_failure(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._install_snap_package") as _install_snap_package,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True
        ) as _is_storage_attached,
    ):
        # Mock the result of the call.
        _install_snap_package.side_effect = snap.SnapError
        # Trigger the hook.
        harness.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_package.assert_called_once()
        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_patroni_scrape_config(harness):
    result = harness.charm.patroni_scrape_config()

    assert result == [
        {
            "metrics_path": "/metrics",
            "scheme": "https",
            "static_configs": [{"targets": ["192.0.2.0:8008"]}],
            "tls_config": {"insecure_skip_verify": True},
        },
    ]


def test_primary_endpoint(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._units_ips",
            new_callable=PropertyMock,
            return_value={"1.1.1.1", "1.1.1.2"},
        ),
        patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock) as _patroni,
    ):
        _patroni.return_value.get_member_ip.return_value = "1.1.1.1"
        _patroni.return_value.get_primary.return_value = sentinel.primary
        assert harness.charm.primary_endpoint == "1.1.1.1"

        _patroni.return_value.get_member_ip.assert_called_once_with(sentinel.primary)
        _patroni.return_value.get_primary.assert_called_once_with()


def test_primary_endpoint_no_peers(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=None
        ),
        patch(
            "charm.PostgresqlOperatorCharm._units_ips",
            new_callable=PropertyMock,
            return_value={"1.1.1.1", "1.1.1.2"},
        ),
        patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock) as _patroni,
    ):
        assert harness.charm.primary_endpoint is None

        assert not _patroni.return_value.get_member_ip.called
        assert not _patroni.return_value.get_primary.called


def test_on_leader_elected(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.TLS.generate_internal_peer_cert"),
    ):
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
    with (
        patch(
            "charm.PostgresqlOperatorCharm._update_member_ip", return_value=False
        ) as _update_member_ip,
        patch(
            "charm.PostgresqlOperatorCharm._validate_config_options"
        ) as _validate_config_options,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch(
            "charm.PostgresqlOperatorCharm.updated_synchronous_node_count", return_value=True
        ) as _updated_synchronous_node_count,
        patch(
            "charm.PostgresqlOperatorCharm.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch(
            "charm.PostgresqlOperatorCharm.is_cluster_initialised", new_callable=PropertyMock
        ) as _is_cluster_initialised,
        patch("charm.PostgresqlOperatorCharm.update_endpoint_addresses"),
    ):
        # Test when the cluster was not initialised yet.
        _is_cluster_initialised.return_value = False
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_not_called()

        # Test when the unit is not the leader.
        _is_cluster_initialised.return_value = True
        harness.charm.on.config_changed.emit()
        _validate_config_options.assert_called_once()
        _enable_disable_extensions.assert_not_called()

        # Test unable to connect to db
        _update_config.reset_mock()
        _validate_config_options.side_effect = OperationalError
        harness.charm.on.config_changed.emit()
        assert not _update_config.called
        _validate_config_options.side_effect = None
        _updated_synchronous_node_count.assert_called_once_with()

        # Test after the cluster was initialised.
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()

        # Test when the unit is in a blocked state due to extensions request,
        # but there are no established legacy relations.
        _enable_disable_extensions.reset_mock()
        harness.charm.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        harness.charm.on.config_changed.emit()
        _enable_disable_extensions.assert_called_once()

        # Test when there is an error related to the config options.
        _update_member_ip.reset_mock()
        _enable_disable_extensions.reset_mock()
        harness.charm.unit.status = BlockedStatus("Configuration Error")
        harness.charm.on.config_changed.emit()
        assert isinstance(harness.model.unit.status, ActiveStatus)
        _update_member_ip.assert_called_once()
        _enable_disable_extensions.assert_called_once()

        # Test when the unit has updated its member IP.
        _update_member_ip.reset_mock()
        _enable_disable_extensions.reset_mock()
        _update_member_ip.return_value = True
        harness.charm.on.config_changed.emit()
        _update_member_ip.assert_called_once()
        _enable_disable_extensions.assert_not_called()


def test_check_extension_dependencies(harness):
    with (
        patch("charm.Patroni.get_primary") as _get_primary,
        patch("subprocess.check_output", return_value=b"C"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()),
        patch("charm.PostgresqlOperatorCharm.update_endpoint_addresses"),
    ):
        _get_primary.return_value = harness.charm.unit

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
    with (
        patch("charm.Patroni.get_primary") as _get_primary,
        patch("charm.PostgresqlOperatorCharm._unit_ip"),
        patch("charm.PostgresqlOperatorCharm._patroni"),
        patch("subprocess.check_output", return_value=b"C"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
    ):
        _get_primary.return_value = harness.charm.unit

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
  synchronous_node_count:
    type: string
    default: "all"
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
  plugin_timescaledb_enable:
    default: false
    type: boolean
  plugin_audit_enable:
    default: true
    type: boolean
  profile:
    default: production
    type: string"""
            new_harness = Harness(PostgresqlOperatorCharm, config=config)
            new_harness.cleanup()
            new_harness.begin()
            new_harness.charm.enable_disable_extensions()
            assert postgresql_mock.enable_disable_extensions.call_count == 1

            # Block if extension-dependent object error is raised
            postgresql_mock.reset_mock()
            postgresql_mock.enable_disable_extensions.side_effect = [
                psycopg2.errors.DependentObjectsStillExist,
                None,
            ]
            harness.charm.enable_disable_extensions()
            assert isinstance(harness.charm.unit.status, BlockedStatus)
            # Should resolve afterwards
            harness.charm.enable_disable_extensions()
            assert isinstance(harness.charm.unit.status, ActiveStatus)


def test_on_start(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._restart_services_after_reboot"
        ) as _restart_services_after_reboot,
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
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
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            side_effect=[False, True, True, True, True, True],
        ) as _is_storage_attached,
        patch(
            "charm.PostgresqlOperatorCharm._can_connect_to_postgresql",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLS.generate_internal_peer_cert"),
    ):
        _get_postgresql_version.return_value = "16.6"

        # Test without storage.
        harness.charm.on.start.emit()
        _reboot_on_detached_storage.assert_called_once()

        # Test before the passwords are generated.
        _member_started.return_value = False
        _get_password.return_value = None
        harness.charm.on.start.emit()
        _bootstrap_cluster.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)

        # ModelError in get password
        _get_password.side_effect = ModelError
        harness.charm.on.start.emit()
        _bootstrap_cluster.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)

        # Mock the passwords.
        _get_password.side_effect = None
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
        _restart_services_after_reboot.assert_called_once()
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # Set an initial waiting status (like after the install hook was triggered).
        harness.model.unit.status = WaitingStatus("fake message")

        # Test the event of an error happening when trying to create the default postgres user.
        _restart_services_after_reboot.reset_mock()
        _member_started.return_value = True
        harness.charm.on.start.emit()
        _postgresql.create_user.assert_called_once()
        _oversee_users.assert_not_called()
        _restart_services_after_reboot.assert_called_once()
        assert isinstance(harness.model.unit.status, BlockedStatus)

        # Set an initial waiting status again (like after the install hook was triggered).
        harness.model.unit.status = WaitingStatus("fake message")

        # Then test the event of a correct cluster bootstrapping.
        _restart_services_after_reboot.reset_mock()
        harness.charm.on.start.emit()
        assert _postgresql.create_user.call_count == 3  # Considering the previous failed call.
        _oversee_users.assert_called_once()
        _enable_disable_extensions.assert_called_once()
        _set_primary_status_message.assert_called_once()
        _restart_services_after_reboot.assert_called_once()


def test_on_start_replica(harness):
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.Patroni.get_postgresql_version") as _get_postgresql_version,
        patch(
            "charm.PostgresqlOperatorCharm._restart_services_after_reboot"
        ) as _restart_services_after_reboot,
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
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            return_value=True,
        ) as _is_storage_attached,
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLS.generate_internal_peer_cert"),
    ):
        _get_postgresql_version.return_value = "16.6"

        # Set the current unit to be a replica (non leader unit).
        harness.set_leader(False)

        # Mock the passwords.
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"

        # Test an uninitialized cluster.
        harness.charm._peers.data[harness.charm.app].update({"cluster_initialised": ""})
        harness.charm.on.start.emit()
        _defer.assert_called_once()
        _restart_services_after_reboot.assert_called_once()

        # Set an initial waiting status again (like after a machine restart).
        harness.model.unit.status = WaitingStatus("fake message")

        # Mark the cluster as initialised and with the workload up and running.
        _restart_services_after_reboot.reset_mock()
        harness.charm._peers.data[harness.charm.app].update({"cluster_initialised": "True"})
        _member_started.return_value = True
        harness.charm.on.start.emit()
        _configure_patroni_on_unit.assert_not_called()
        _restart_services_after_reboot.assert_called_once()
        assert isinstance(harness.model.unit.status, ActiveStatus)

        # Set an initial waiting status (like after the install hook was triggered).
        harness.model.unit.status = WaitingStatus("fake message")

        # Check that the unit status doesn't change when the workload is not running.
        # In that situation only Patroni is configured in the unit (but not started).
        _restart_services_after_reboot.reset_mock()
        _member_started.return_value = False
        harness.charm.on.start.emit()
        _configure_patroni_on_unit.assert_called_once()
        _restart_services_after_reboot.assert_called_once()
        assert isinstance(harness.model.unit.status, WaitingStatus)


def test_on_start_no_patroni_member(harness):
    with (
        patch("subprocess.check_output", return_value=b"C"),
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch("charm.Patroni") as patroni,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True
        ) as _is_storage_attached,
        patch("charm.PostgresqlOperatorCharm.get_available_memory") as _get_available_memory,
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLS.generate_internal_peer_cert"),
    ):
        # Mock the passwords.
        patroni.return_value.member_started = False
        _get_password.return_value = "fake-operator-password"
        bootstrap_cluster = patroni.return_value.bootstrap_cluster
        bootstrap_cluster.return_value = True

        patroni.return_value.get_postgresql_version.return_value = "16.6"

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


def test_on_update_status(harness):
    with (
        patch("charm.ClusterTopologyObserver.start_observer") as _start_observer,
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
        patch("charm.Patroni.restart_patroni") as _restart_patroni,
        patch("charm.Patroni.is_member_isolated") as _is_member_isolated,
        patch(
            "charm.Patroni.member_replication_lag", new_callable=PropertyMock
        ) as _member_replication_lag,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_leader", new_callable=PropertyMock
        ) as _is_standby_leader,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value=True),
        ) as _primary_endpoint,
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch("charm.Patroni.last_postgresql_logs") as _last_postgresql_logs,
        patch("charm.Patroni.patroni_logs") as _patroni_logs,
        patch("charm.Patroni.get_member_status") as _get_member_status,
        patch(
            "charm.PostgreSQLBackups.can_use_s3_repository", return_value=(True, None)
        ) as _can_use_s3_repository,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm.log_pitr_last_transaction_time"),
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test before the cluster is initialised.
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()

        # Test after the cluster was initialised, but with the unit in a blocked state.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"cluster_initialised": "True"}
            )
        harness.charm.unit.status = BlockedStatus("fake blocked status")
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()

        # Test the point-in-time-recovery fail.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {
                    "cluster_initialised": "True",
                    "restoring-backup": "valid",
                    "restore-to-time": "valid",
                },
            )
        harness.charm.unit.status = ActiveStatus()
        _patroni_logs.return_value = "2022-02-24 02:00:00 UTC patroni.exceptions.PatroniFatalException: Failed to bootstrap cluster"
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_not_called()
        assert harness.charm.unit.status.message == CANNOT_RESTORE_PITR

        # Test with the unit in a status different that blocked.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True", "restoring-backup": "", "restore-to-time": ""},
            )
        harness.charm.unit.status = ActiveStatus()
        harness.charm.on.update_status.emit()
        _set_primary_status_message.assert_called_once()

        # Test call to restart when the member is isolated from the cluster.
        _set_primary_status_message.reset_mock()
        _member_started.return_value = False
        _is_member_isolated.return_value = True
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.unit.name, {"postgresql_restarted": ""}
            )
        harness.charm.on.update_status.emit()
        _restart_patroni.assert_called_once()
        _start_observer.assert_called_once()


def test_on_update_status_after_restore_operation(harness):
    with (
        patch("charm.ClusterTopologyObserver.start_observer"),
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
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
        patch(
            "charms.postgresql_k8s.v1.postgresql.PostgreSQL.get_current_timeline"
        ) as _get_current_timeline,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.Patroni.get_member_status") as _get_member_status,
    ):
        _get_current_timeline.return_value = "2"
        rel_id = harness.model.get_relation(PEER).id
        # Test when the restore operation fails.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True", "restoring-backup": "20230101-090000F"},
            )
        _get_member_status.return_value = "failed"
        harness.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
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
        _set_primary_status_message.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is still in the application relation databag.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True",
            "restoring-backup": "20230101-090000F",
        }

        # Test when the restore operation finished successfully.
        _member_started.return_value = True
        _can_use_s3_repository.return_value = (True, None)
        _handle_processes_failures.return_value = False
        harness.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_called_once()
        _oversee_users.assert_called_once()
        _update_relation_endpoints.assert_called_once()
        _set_primary_status_message.assert_called_once()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is not in the application relation databag anymore.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True"
        }

        # Test when it's not possible to use the configured S3 repository.
        _update_config.reset_mock()
        _set_primary_status_message.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"restoring-backup": "20230101-090000F"},
            )
        _can_use_s3_repository.return_value = (False, "fake validation message")
        harness.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _set_primary_status_message.assert_called_once()
        # Assert that the backup id is not in the application relation databag anymore.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True",
            "s3-initialization-block-message": "fake validation message",
        }


def test_install_snap_package(harness):
    with patch("charm.snap.SnapCache") as _snap_cache:
        _snap_package = _snap_cache.return_value.__getitem__.return_value
        _snap_package.ensure.side_effect = snap.SnapError
        _snap_package.present = False

        with pathlib.Path("refresh_versions.toml").open("rb") as file:
            _revision = tomli.load(file)["snap"]["revisions"][platform.machine()]

        # Test for problem with snap update.
        with pytest.raises(snap.SnapError):
            harness.charm._install_snap_package(revision=None)
        _snap_cache.return_value.__getitem__.assert_called_once_with("charmed-postgresql")
        _snap_cache.assert_called_once_with()
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Present, revision=_revision)

        # Test with a not found package.
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = snap.SnapNotFoundError
        with pytest.raises(snap.SnapNotFoundError):
            harness.charm._install_snap_package(revision=None)
        _snap_cache.return_value.__getitem__.assert_called_once_with("charmed-postgresql")
        _snap_cache.assert_called_once_with()
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Present, revision=_revision)

        # Then test a valid one.
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = None
        harness.charm._install_snap_package(revision=None)
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("charmed-postgresql")
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Present, revision=_revision)
        _snap_package.hold.assert_called_once_with()

        # Test revision
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.ensure.side_effect = None
        harness.charm._install_snap_package(revision="42")
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("charmed-postgresql")
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Present, revision="42")
        _snap_package.hold.assert_called_once_with()

        # Test with refresh
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.present = True
        _refresh = Mock()
        harness.charm._install_snap_package(
            revision="42",
            refresh=_refresh,
        )
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("charmed-postgresql")
        _snap_package.ensure.assert_called_once_with(snap.SnapState.Present, revision="42")
        _snap_package.hold.assert_called_once_with()
        _refresh.update_snap_revision.assert_called_once()

        # Test without refresh
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        harness.charm._install_snap_package(revision="42")
        _snap_cache.assert_called_once_with()
        _snap_cache.return_value.__getitem__.assert_called_once_with("charmed-postgresql")
        _snap_package.ensure.assert_not_called()
        _snap_package.hold.assert_not_called()

        # test missing architecture
        _snap_cache.reset_mock()
        _snap_package.reset_mock()
        _snap_package.present = True
        with patch("platform.machine") as _machine:
            _machine.return_value = "missingarch"
            with pytest.raises(KeyError):
                harness.charm._install_snap_package(revision=None)
        assert not _snap_package.ensure.called
        assert not _snap_package.hold.called


def test_is_storage_attached(harness):
    with patch(
        "subprocess.check_call",
        side_effect=[None, subprocess.CalledProcessError(1, "fake command")],
    ) as _check_call:
        # Test with attached storage.
        is_storage_attached = harness.charm._is_storage_attached()
        _check_call.assert_called_once_with([
            "/usr/bin/mountpoint",
            "-q",
            harness.charm._storage_path,
        ])
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
        _check_call.assert_called_once_with(["/usr/bin/systemctl", "reboot"])


def test_restart(harness):
    with (
        patch("charm.Patroni.restart_postgresql") as _restart_postgresql,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
        patch("charm.PostgresqlOperatorCharm._can_connect_to_postgresql", return_value=True),
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


def test_update_config(harness):
    with pathlib.Path("refresh_versions.toml").open("rb") as file:
        _revision = tomli.load(file)["snap"]["revisions"][platform.machine()]

    class _MockSnap:
        revision = _revision

    with (
        patch("subprocess.check_output", return_value=b"C"),
        patch("charm.snap.SnapCache", lambda: {"charmed-postgresql": _MockSnap()}),
        patch(
            "charm.PostgresqlOperatorCharm._handle_postgresql_restart_need"
        ) as _handle_postgresql_restart_need,
        patch(
            "charm.PostgresqlOperatorCharm._restart_metrics_service"
        ) as _restart_metrics_service,
        patch(
            "charm.PostgresqlOperatorCharm._restart_ldap_sync_service"
        ) as _restart_ldap_sync_service,
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
        patch("charm.PostgresqlOperatorCharm.get_available_memory") as _get_available_memory,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Mock some properties.
        postgresql_mock.is_tls_enabled = PropertyMock(side_effect=[False, False, False, False])
        _is_workload_running.side_effect = [True, True, False, True]
        _member_started.side_effect = [True, True, False]
        postgresql_mock.build_postgresql_parameters.return_value = {"test": "test"}

        # Test without TLS files available.
        with harness.hooks_disabled():
            harness.update_relation_data(rel_id, harness.charm.unit.name, {"tls": ""})
        _is_tls_enabled.return_value = False
        harness.charm.update_config()
        _render_patroni_yml_file.assert_called_once_with(
            connectivity=True,
            is_creating_backup=False,
            enable_ldap=False,
            enable_tls=False,
            backup_id=None,
            stanza=None,
            restore_stanza=None,
            restore_timeline=None,
            pitr_target=None,
            restore_to_latest=False,
            parameters={"test": "test"},
            no_peers=False,
            user_databases_map={"operator": "all", "replication": "all", "rewind": "all"},
        )
        _handle_postgresql_restart_need.assert_called_once_with()
        _restart_ldap_sync_service.assert_called_once()
        _restart_metrics_service.assert_called_once()
        assert "tls" not in harness.get_relation_data(rel_id, harness.charm.unit.name)

        # Test with TLS files available.
        _handle_postgresql_restart_need.reset_mock()
        _restart_ldap_sync_service.reset_mock()
        _restart_metrics_service.reset_mock()
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it change.
        _is_tls_enabled.return_value = True
        _render_patroni_yml_file.reset_mock()
        harness.charm.update_config()
        _render_patroni_yml_file.assert_called_once_with(
            connectivity=True,
            is_creating_backup=False,
            enable_ldap=False,
            enable_tls=True,
            backup_id=None,
            stanza=None,
            restore_stanza=None,
            restore_timeline=None,
            pitr_target=None,
            restore_to_latest=False,
            parameters={"test": "test"},
            no_peers=False,
            user_databases_map={"operator": "all", "replication": "all", "rewind": "all"},
        )
        _handle_postgresql_restart_need.assert_called_once()
        _restart_ldap_sync_service.assert_called_once()
        _restart_metrics_service.assert_called_once()
        assert "tls" not in harness.get_relation_data(
            rel_id, harness.charm.unit.name
        )  # The "tls" flag is set in handle_postgresql_restart_need.

        # Test with workload not running yet.
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it change.
        _handle_postgresql_restart_need.reset_mock()
        _restart_ldap_sync_service.reset_mock()
        _restart_metrics_service.reset_mock()
        harness.charm.update_config()
        _handle_postgresql_restart_need.assert_not_called()
        assert harness.get_relation_data(rel_id, harness.charm.unit.name)["tls"] == "enabled"

        # Test with member not started yet.
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it doesn't change.
        _is_tls_enabled.return_value = False
        harness.charm.update_config()
        _handle_postgresql_restart_need.assert_not_called()
        _restart_ldap_sync_service.assert_not_called()
        _restart_metrics_service.assert_not_called()
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
    ):
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = []
        _charm_lib.return_value.validate_date_style.return_value = False
        _charm_lib.return_value.validate_group_map.return_value = False
        _charm_lib.return_value.get_postgresql_timezones.return_value = []

        # Test instance_default_text_search_config exception
        with harness.hooks_disabled():
            harness.update_config({"instance_default_text_search_config": "pg_catalog.test"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert (
            str(e.value)
            == "instance_default_text_search_config config option has an invalid value"
        )

        _charm_lib.return_value.get_postgresql_text_search_configs.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = [
            "pg_catalog.test"
        ]

        # Test ldap_map exception
        with harness.hooks_disabled():
            harness.update_config({"ldap_map": "ldap_group="})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert str(e.value) == "ldap_map config option has an invalid value"

        _charm_lib.return_value.validate_group_map.assert_called_once_with("ldap_group=")
        _charm_lib.return_value.validate_group_map.return_value = True

        # Test request_date_style exception
        with harness.hooks_disabled():
            harness.update_config({"request_date_style": "ISO, TEST"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert str(e.value) == "request_date_style config option has an invalid value"

        _charm_lib.return_value.validate_date_style.assert_called_once_with("ISO, TEST")
        _charm_lib.return_value.validate_date_style.return_value = True

        # Test request_time_zone exception
        with harness.hooks_disabled():
            harness.update_config({"request_time_zone": "TEST_ZONE"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert str(e.value) == "request_time_zone config option has an invalid value"

        _charm_lib.return_value.get_postgresql_timezones.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_timezones.return_value = ["TEST_ZONE"]

        # Test locales exception
        with harness.hooks_disabled():
            harness.update_config({"response_lc_monetary": "test_TEST"})

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        message = "1 validation error for CharmConfig\nresponse_lc_monetary\n  Input should be "
        assert str(e.value).startswith(message)


def test_on_peer_relation_changed(harness):
    with (
        patch("charm.snap.SnapCache"),
        patch("charm.PostgresqlOperatorCharm._update_new_unit_status") as _update_new_unit_status,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
        patch("backups.PostgreSQLBackups.coordinate_stanza_fields") as _coordinate_stanza_fields,
        patch(
            "backups.PostgreSQLBackups.start_stop_pgbackrest_service"
        ) as _start_stop_pgbackrest_service,
        patch("charm.Patroni.reinitialize_postgresql") as _reinitialize_postgresql,
        patch(
            "charm.Patroni.member_replication_lag", new_callable=PropertyMock
        ) as _member_replication_lag,
        patch("charm.PostgresqlOperatorCharm.is_standby_leader") as _is_standby_leader,
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
        mock_event.unit = None

        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"cluster_initialised": ""}
            )
        harness.charm._on_peer_relation_changed(mock_event)
        _reconfigure_cluster.assert_not_called()

        # Test an initialized cluster and this is the leader unit
        # (but it fails to reconfigure the cluster).
        mock_event.defer.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True", "members_ips": '["192.0.2.0"]'},
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
        _member_started.return_value = True
        _primary_endpoint.return_value = "192.0.2.0"
        harness.model.unit.status = WaitingStatus("awaiting for cluster to start")
        harness.charm._on_peer_relation_changed(mock_event)
        mock_event.defer.assert_not_called()
        _reconfigure_cluster.assert_called_once_with(mock_event)
        _update_config.assert_called_once()
        _start_patroni.assert_called_once()
        _update_new_unit_status.assert_called_once()

        # Test when the unit fails to update the Patroni configuration.
        _update_config.reset_mock()
        _start_patroni.reset_mock()
        _update_new_unit_status.reset_mock()
        _update_config.side_effect = RetryError(last_attempt=1)
        harness.charm._on_peer_relation_changed(mock_event)
        _update_config.assert_called_once()
        _start_patroni.assert_not_called()
        _update_new_unit_status.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)

        # Test event is early exiting when in blocked status.
        _update_config.side_effect = None
        _member_started.return_value = False
        harness.charm._on_peer_relation_changed(mock_event)
        _start_patroni.assert_not_called()

        # Test when Patroni hasn't started yet in the unit.
        harness.model.unit.status = ActiveStatus()
        _update_config.side_effect = None
        _member_started.return_value = False
        harness.charm._on_peer_relation_changed(mock_event)
        _start_patroni.assert_called_once()
        _update_new_unit_status.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)

        # Test when Patroni has already started but this is a replica with a
        # huge or unknown lag.
        relation = harness.model.get_relation(PEER, rel_id)
        _member_started.return_value = True
        for values in itertools.product([True, False], ["0", "1000", "1001", "unknown"]):
            _defer.reset_mock()
            _start_stop_pgbackrest_service.reset_mock()
            _is_primary.return_value = values[0]
            _is_standby_leader.return_value = values[0]
            _member_replication_lag.return_value = values[1]
            harness.charm.unit.status = ActiveStatus()
            harness.charm.on.database_peers_relation_changed.emit(relation)
            if _is_primary.return_value == values[0] or int(values[1]) <= 1000:
                _defer.assert_not_called()
                _start_stop_pgbackrest_service.assert_called_once()
                assert isinstance(harness.charm.unit.status, ActiveStatus)
            else:
                _defer.assert_called_once()
                _start_stop_pgbackrest_service.assert_not_called()
                assert isinstance(harness.charm.unit.status, MaintenanceStatus)

        # Test when it was not possible to start the pgBackRest service yet.
        relation = harness.model.get_relation(PEER, rel_id)
        _member_started.return_value = True
        _defer.reset_mock()
        _coordinate_stanza_fields.reset_mock()
        _start_stop_pgbackrest_service.return_value = False
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_called_once()
        _coordinate_stanza_fields.assert_not_called()

        # Test the last calls been made when it was possible to start the
        # pgBackRest service.
        _defer.reset_mock()
        _start_stop_pgbackrest_service.return_value = True
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_not_called()
        _coordinate_stanza_fields.assert_called_once()


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
        patch("charm.TLS.get_client_tls_files") as _get_client_tls_files,
        patch("charm.TLS.refresh_tls_certificates_event") as _refresh_tls_certificates_event,
    ):
        # If there is no current TLS files, _request_certificate should be called
        # only when the certificates relation is established.
        _get_client_tls_files.return_value = (None, None, None)
        harness.charm._update_certificate()
        _refresh_tls_certificates_event.emit.assert_not_called()

        # Test with already present TLS files (when they will be replaced by new ones).
        _get_client_tls_files.return_value = (sentinel.key, sentinel.ca, sentinel.cert)

        harness.charm._update_certificate()
        _refresh_tls_certificates_event.emit.assert_called_once_with()


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
                    "ip": "192.0.2.0",
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
        assert relation_data.get("ip") == "192.0.2.0"
        assert relation_data.get("ip-to-remove") == "2.2.2.2"
        _stop_patroni.assert_called_once()
        _update_certificate.assert_called_once()


def test_push_tls_files_to_workload(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.render_file") as _render_file,
        patch("charm.TLS.get_client_tls_files") as _get_client_tls_files,
        patch("charm.TLS.get_peer_tls_files") as _get_peer_tls_files,
        patch(
            "charm.PostgresqlOperatorCharm.get_secret", return_value="internal_ca"
        ) as _get_secret,
    ):
        _get_client_tls_files.side_effect = [
            ("key", "ca", "cert"),
            ("key", "ca", None),
            ("key", None, "cert"),
            (None, "ca", "cert"),
        ]
        _get_peer_tls_files.side_effect = [
            ("key", "ca", "cert"),
            ("key", "ca", None),
            ("key", None, "cert"),
            (None, "ca", "cert"),
        ]
        _update_config.side_effect = [True, False, False, False]

        # Test when all TLS files are available.
        assert harness.charm.push_tls_files_to_workload()
        assert _render_file.call_count == 7

        # Test when not all TLS files are available.
        for _ in range(3):
            _render_file.reset_mock()
            assert not (harness.charm.push_tls_files_to_workload())
            assert _render_file.call_count == 5


def test_push_ca_file_into_workload(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("pathlib.Path.write_text") as _write_text,
        patch("subprocess.check_call") as _check_call,
    ):
        harness.charm.set_secret("unit", "ca-app", "test-ca")

        assert harness.charm.push_ca_file_into_workload("ca-app")
        _write_text.assert_called_once()
        _check_call.assert_called_once_with([UPDATE_CERTS_BIN_PATH])
        _update_config.assert_called_once()


def test_clean_ca_file_from_workload(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("pathlib.Path.write_text") as _write_text,
        patch("pathlib.Path.unlink") as _unlink,
        patch("subprocess.check_call") as _check_call,
    ):
        harness.charm.set_secret("unit", "ca-app", "test-ca")

        assert harness.charm.push_ca_file_into_workload("ca-app")
        _write_text.assert_called_once()
        _check_call.assert_called_once_with([UPDATE_CERTS_BIN_PATH])
        _update_config.assert_called_once()

        _check_call.reset_mock()
        _update_config.reset_mock()

        assert harness.charm.clean_ca_file_from_workload("ca-app")
        _unlink.assert_called_once()
        _check_call.assert_called_once_with([UPDATE_CERTS_BIN_PATH])


def test_is_workload_running(harness):
    with patch("charm.snap.SnapCache") as _snap_cache:
        pg_snap = _snap_cache.return_value[charm_refresh.snap_name()]

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


def test_juju_run_exec(harness):
    with (
        patch("charm.ClusterTopologyObserver") as _topology_observer,
    ):
        # Juju 3
        harness = Harness(PostgresqlOperatorCharm)
        harness.begin()
        _topology_observer.assert_called_once_with(harness.charm, "/usr/bin/juju-exec")


def test_client_relations(harness):
    # Test when the charm has no relations.
    assert len(harness.charm.client_relations) == 0

    # Test when the charm has some relations.
    harness.add_relation("database", "application")
    database_relation = harness.model.get_relation("database")
    assert harness.charm.client_relations == [database_relation]


def test_add_cluster_member(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._get_unit_ip", return_value="1.1.1.1"),
        patch("charm.PostgresqlOperatorCharm._add_to_members_ips") as _add_to_members_ips,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
    ):
        harness.charm.add_cluster_member("postgresql-0")

        _add_to_members_ips.assert_called_once_with("1.1.1.1")
        _update_config.assert_called_once_with()
        _update_config.reset_mock()

        # Charm blocks when update_config fails
        _update_config.side_effect = RetryError(last_attempt=None)
        harness.charm.add_cluster_member("postgresql-0")
        _update_config.assert_called_once_with()
        assert isinstance(harness.charm.unit.status, BlockedStatus)
        assert harness.charm.unit.status.message == "failed to update cluster members on member"
        _update_config.reset_mock()

        # Not ready error if not all members are ready
        _are_all_members_ready.return_value = False
        with pytest.raises(NotReadyError):
            harness.charm.add_cluster_member("postgresql-0")


def test_stuck_raft_cluster_check(harness):
    # doesn't raise flags if there are no raft flags
    assert not harness.charm._stuck_raft_cluster_check()

    # Raft is stuck
    rel_id = harness.model.get_relation(PEER).id
    with harness.hooks_disabled():
        harness.set_leader()
        harness.update_relation_data(rel_id, harness.charm.unit.name, {"raft_stuck": "True"})

    harness.charm._stuck_raft_cluster_check()
    assert "raft_selected_candidate" not in harness.charm.app_peer_data

    # Raft candidate
    with harness.hooks_disabled():
        harness.update_relation_data(rel_id, harness.charm.unit.name, {"raft_candidate": "True"})
    harness.charm._stuck_raft_cluster_check()
    assert harness.charm.app_peer_data["raft_selected_candidate"] == harness.charm.unit.name

    # Don't override existing candidate
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id, harness.charm.app.name, {"raft_selected_candidate": "something_else"}
        )
    harness.charm._stuck_raft_cluster_check()
    assert harness.charm.app_peer_data["raft_selected_candidate"] != harness.charm.unit.name


def test_stuck_raft_cluster_cleanup(harness):
    rel_id = harness.model.get_relation(PEER).id

    # Cleans up app data
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id,
            harness.charm.app.name,
            {
                "raft_rejoin": "True",
                "raft_reset_primary": "True",
                "raft_selected_candidate": "unit_name",
            },
        )
    harness.charm._stuck_raft_cluster_cleanup()

    assert "raft_rejoin" not in harness.charm.app_peer_data
    assert "raft_reset_primary" not in harness.charm.app_peer_data
    assert "raft_selected_candidate" not in harness.charm.app_peer_data

    # Don't clean up if there's unit data flags
    with harness.hooks_disabled():
        harness.update_relation_data(rel_id, harness.charm.unit.name, {"raft_primary": "True"})
        harness.update_relation_data(
            rel_id,
            harness.charm.app.name,
            {
                "raft_rejoin": "True",
                "raft_reset_primary": "True",
                "raft_selected_candidate": "unit_name",
            },
        )
    harness.charm._stuck_raft_cluster_cleanup()

    assert "raft_rejoin" in harness.charm.app_peer_data
    assert "raft_reset_primary" in harness.charm.app_peer_data
    assert "raft_selected_candidate" in harness.charm.app_peer_data


def test_stuck_raft_cluster_rejoin(harness):
    rel_id = harness.model.get_relation(PEER).id

    with (
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch("charm.PostgresqlOperatorCharm._add_to_members_ips") as _add_to_members_ips,
    ):
        # No data
        harness.charm._stuck_raft_cluster_rejoin()

        assert "raft_reset_primary" not in harness.charm.app_peer_data
        assert "raft_rejoin" not in harness.charm.app_peer_data

        # Raises primary flag
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.unit.name,
                {
                    "raft_primary": "test_primary",
                    f"{PEER}-address": "192.0.2.0",
                },
            )
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"raft_followers_stopped": "test_candidate"},
            )

        harness.charm._stuck_raft_cluster_rejoin()

        assert "raft_reset_primary" in harness.charm.app_peer_data
        assert "raft_rejoin" in harness.charm.app_peer_data
        assert "members_ips" not in harness.charm.app_peer_data
        _add_to_members_ips.assert_called_once_with("192.0.2.0")
        _update_relation_endpoints.assert_called_once_with()


def test_raft_reinitialisation(harness):
    rel_id = harness.model.get_relation(PEER).id

    with (
        patch(
            "charm.PostgresqlOperatorCharm._stuck_raft_cluster_check"
        ) as _stuck_raft_cluster_check,
        patch(
            "charm.PostgresqlOperatorCharm._stuck_raft_cluster_rejoin"
        ) as _stuck_raft_cluster_rejoin,
        patch(
            "charm.PostgresqlOperatorCharm._stuck_raft_cluster_cleanup"
        ) as _stuck_raft_cluster_cleanup,
        patch("charm.Patroni.remove_raft_data") as _remove_raft_data,
        patch("charm.Patroni.reinitialise_raft_data") as _reinitialise_raft_data,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._set_primary_status_message"),
    ):
        # No data
        harness.charm._raft_reinitialisation()

        # Different candidate
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id, harness.charm.unit.name, {"raft_stuck": "True", "raft_candidate": "True"}
            )
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"raft_selected_candidate": "test_candidate"},
            )

        harness.charm._raft_reinitialisation()
        _stuck_raft_cluster_rejoin.assert_called_once_with()
        _stuck_raft_cluster_check.assert_called_once_with()
        assert not _stuck_raft_cluster_cleanup.called
        _remove_raft_data.assert_called_once_with()
        assert not _reinitialise_raft_data.called

        # Current candidate
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id,
                harness.charm.unit.name,
                {"raft_stuck": "", "raft_candidate": "True", "raft_stopped": "True"},
            )
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"raft_selected_candidate": "postgresql/0", "raft_followers_stopped": "True"},
            )

        harness.charm._raft_reinitialisation()
        _reinitialise_raft_data.assert_called_once_with()

        # Cleanup
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id,
                harness.charm.unit.name,
                {"raft_stuck": "", "raft_candidate": "True", "raft_stopped": "True"},
            )
            harness.update_relation_data(rel_id, harness.charm.app.name, {"raft_rejoin": "True"})
        harness.charm._raft_reinitialisation()
        _stuck_raft_cluster_cleanup.assert_called_once_with()
        _update_config.assert_called_once_with()


#
# Secrets
#


def test_scope_obj(harness):
    assert harness.charm._scope_obj("app") == harness.charm.framework.model.app
    assert harness.charm._scope_obj("unit") == harness.charm.framework.model.unit
    assert harness.charm._scope_obj("test") is None


@pytest.mark.parametrize("scope,field", [("app", "operator-password"), ("unit", "csr")])
def test_get_secret_secrets(harness, scope, field):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        harness.set_leader()

        assert harness.charm.get_secret(scope, field) is None
        harness.charm.set_secret(scope, field, "test")
        assert harness.charm.get_secret(scope, field) == "test"


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
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
def test_invalid_secret(harness, scope, is_leader):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        with pytest.raises((RelationDataTypeError, TypeError)):
            harness.charm.set_secret(scope, "somekey", 1)

        harness.charm.set_secret(scope, "somekey", "")
        assert harness.charm.get_secret(scope, "somekey") is None


def test_delete_password(harness, caplog):
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
        with caplog.at_level(logging.DEBUG):
            error_message = "Non-existing secret operator-password was attempted to be removed."

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
def test_migration_from_single_secret(harness, scope, is_leader):
    """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage.

    Since it checks for a migration from databag to juju secrets, it's specific to juju3.
    """
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
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
        assert harness.charm.model.get_secret(label=f"{PEER}.postgresql.{scope}")
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
            [True, False], [True, False], [True, False], [True, False]
        ):
            _reload_patroni_configuration.reset_mock()
            _restart.reset_mock()
            with harness.hooks_disabled():
                harness.update_relation_data(rel_id, harness.charm.unit.name, {"tls": ""})
                harness.update_relation_data(
                    rel_id,
                    harness.charm.unit.name,
                    {"postgresql_restarted": ("True" if values[3] else "")},
                )

            _is_tls_enabled.return_value = values[0]
            postgresql_mock.is_tls_enabled.return_value = values[1]
            postgresql_mock.is_restart_pending = PropertyMock(return_value=values[2])

            harness.charm._handle_postgresql_restart_need()
            _reload_patroni_configuration.assert_called_once()
            if values[0]:
                assert "tls" in harness.get_relation_data(rel_id, harness.charm.unit)
            else:
                assert "tls" not in harness.get_relation_data(rel_id, harness.charm.unit)

            if (values[0] != values[1]) or values[2]:
                assert "postgresql_restarted" not in harness.get_relation_data(
                    rel_id, harness.charm.unit
                )
                _restart.assert_called_once()
            else:
                if values[3]:
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
            "charm.PostgresqlOperatorCharm.updated_synchronous_node_count"
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
        _updated_synchronous_node_count.assert_called_once_with()
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
        _updated_synchronous_node_count.assert_called_once_with()
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
        _updated_synchronous_node_count.assert_called_once_with()
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
        _updated_synchronous_node_count.assert_called_once_with()
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
        _updated_synchronous_node_count.assert_called_once_with()
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
        _updated_synchronous_node_count.assert_called_once_with()
        _get_ips_to_remove.assert_called_once()
        _remove_from_members_ips.assert_called_once()
        _update_config.assert_called_once()
        _update_relation_endpoints.assert_not_called()
        assert isinstance(harness.charm.unit.status, WaitingStatus)


def test_update_new_unit_status(harness):
    with (
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication.handle_read_only_mode"
        ) as handle_read_only_mode,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints"
        ) as _update_relation_endpoints,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _primary_endpoint,
    ):
        # Test when the primary endpoint is reachable.
        _primary_endpoint.return_value = "endpoint"
        harness.charm.unit.status = MaintenanceStatus("fake status")
        harness.charm._update_new_unit_status()
        _update_relation_endpoints.assert_called_once()
        handle_read_only_mode.assert_called_once()
        assert not isinstance(harness.charm.unit.status, WaitingStatus)

        # Test when the primary endpoint is not reachable yet.
        _update_relation_endpoints.reset_mock()
        handle_read_only_mode.reset_mock()
        _primary_endpoint.return_value = None
        harness.charm._update_new_unit_status()
        _update_relation_endpoints.assert_not_called()
        handle_read_only_mode.assert_not_called()
        assert isinstance(harness.charm.unit.status, WaitingStatus)


@pytest.mark.parametrize("is_leader", [True, False])
def test_set_primary_status_message(harness, is_leader):
    with (
        patch("charm.Patroni.has_raft_quorum", return_value=True),
        patch("charm.Patroni.get_running_cluster_members", return_value=["test"]),
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_leader", new_callable=PropertyMock
        ) as _is_standby_leader,
        patch("charm.Patroni.get_primary") as _get_primary,
    ):
        for values in itertools.product(
            [
                RetryError(last_attempt=1),
                ConnectionError,
                harness.charm.unit.name,
                f"{harness.charm.app.name}/2",
            ],
            [
                RetryError(last_attempt=1),
                ConnectionError,
                True,
                False,
            ],
            [True, False],
        ):
            harness.charm.unit.status = MaintenanceStatus("fake status")
            _member_started.return_value = values[2]
            if isinstance(values[0], str):
                _get_primary.side_effect = None
                _get_primary.return_value = values[0]
                if values[0] != harness.charm.unit.name and not isinstance(values[1], bool):
                    _is_standby_leader.side_effect = values[1]
                    _is_standby_leader.return_value = None
                    harness.charm._set_primary_status_message()
                    assert isinstance(harness.charm.unit.status, MaintenanceStatus)
                else:
                    _is_standby_leader.side_effect = None
                    _is_standby_leader.return_value = (
                        values[0] != harness.charm.unit.name and values[1]
                    )
                    harness.charm._set_primary_status_message()
                    assert isinstance(
                        harness.charm.unit.status,
                        ActiveStatus
                        if values[0] == harness.charm.unit.name or values[1] or values[2]
                        else MaintenanceStatus,
                    )
                    status = (
                        "Primary"
                        if values[0] == harness.charm.unit.name
                        else ("Standby" if values[1] else "" if values[2] else "fake status")
                    )
                    assert harness.charm.unit.status.message == status
            else:
                _get_primary.side_effect = values[0]
                _get_primary.return_value = None
                harness.charm._set_primary_status_message()
                assert isinstance(harness.charm.unit.status, MaintenanceStatus)


def test_override_patroni_restart_condition(harness):
    with (
        patch("charm.Patroni.update_patroni_restart_condition") as _update_restart_condition,
        patch("charm.Patroni.get_patroni_restart_condition") as _get_restart_condition,
        patch("charm.PostgresqlOperatorCharm._unit_ip") as _unit_ip,
    ):
        _get_restart_condition.return_value = "always"

        # Do override without repeat_cause
        assert harness.charm.override_patroni_restart_condition("no", None) is True
        _get_restart_condition.assert_called_once()
        _update_restart_condition.assert_called_once_with("no")
        _get_restart_condition.reset_mock()
        _update_restart_condition.reset_mock()

        _get_restart_condition.return_value = "no"

        # Must not be overridden twice without repeat_cause
        assert harness.charm.override_patroni_restart_condition("on-failure", None) is False
        _get_restart_condition.assert_called_once()
        _update_restart_condition.assert_not_called()
        _get_restart_condition.reset_mock()
        _update_restart_condition.reset_mock()

        # Reset override
        harness.charm.restore_patroni_restart_condition()
        _update_restart_condition.assert_called_once_with("always")
        _update_restart_condition.reset_mock()

        # Must not be reset twice
        harness.charm.restore_patroni_restart_condition()
        _update_restart_condition.assert_not_called()
        _update_restart_condition.reset_mock()

        _get_restart_condition.return_value = "always"

        # Do override with repeat_cause
        assert harness.charm.override_patroni_restart_condition("no", "test_charm") is True
        _get_restart_condition.assert_called_once()
        _update_restart_condition.assert_called_once_with("no")
        _get_restart_condition.reset_mock()
        _update_restart_condition.reset_mock()

        _get_restart_condition.return_value = "no"

        # Do re-override with repeat_cause
        assert harness.charm.override_patroni_restart_condition("on-success", "test_charm") is True
        _get_restart_condition.assert_called_once()
        _update_restart_condition.assert_called_once_with("on-success")
        _get_restart_condition.reset_mock()
        _update_restart_condition.reset_mock()

        _get_restart_condition.return_value = "on-success"

        # Must not be re-overridden with different repeat_cause
        assert (
            harness.charm.override_patroni_restart_condition("on-failure", "test_not_charm")
            is False
        )
        _get_restart_condition.assert_called_once()
        _update_restart_condition.assert_not_called()
        _get_restart_condition.reset_mock()
        _update_restart_condition.reset_mock()

        # Reset override
        harness.charm.restore_patroni_restart_condition()
        _update_restart_condition.assert_called_once_with("always")
        _update_restart_condition.reset_mock()


def test_restart_services_after_reboot(harness):
    with (
        patch(
            "backups.PostgreSQLBackups.start_stop_pgbackrest_service"
        ) as _start_stop_pgbackrest_service,
        patch("charm.Patroni.start_patroni") as _start_patroni,
        patch(
            "charm.PostgresqlOperatorCharm._unit_ip",
            new_callable=PropertyMock(return_value="1.1.1.1"),
        ) as _unit_ip,
    ):
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER).id,
                harness.charm.app.name,
                {"members_ips": json.dumps([])},
            )
        harness.charm._restart_services_after_reboot()
        _start_patroni.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER).id,
                harness.charm.app.name,
                {"members_ips": json.dumps([_unit_ip])},
            )
        harness.charm._restart_services_after_reboot()
        _start_patroni.assert_called_once()
        _start_stop_pgbackrest_service.assert_called_once()


def test_get_plugins(harness):
    with patch("charm.PostgresqlOperatorCharm._on_config_changed"):
        # Test when the charm has no plugins enabled.
        assert harness.charm.get_plugins() == ["pgaudit"]

        # Test when the charm has some plugins enabled.
        harness.update_config({
            "plugin_audit_enable": True,
            "plugin_citext_enable": True,
            "plugin_spi_enable": True,
        })
        assert harness.charm.get_plugins() == [
            "pgaudit",
            "citext",
            "refint",
            "autoinc",
            "insert_username",
            "moddatetime",
        ]

        # Test when the charm has the pgAudit plugin disabled.
        harness.update_config({"plugin_audit_enable": False})
        assert harness.charm.get_plugins() == [
            "citext",
            "refint",
            "autoinc",
            "insert_username",
            "moddatetime",
        ]


def test_on_promote_to_primary(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._raft_reinitialisation") as _raft_reinitialisation,
        patch("charm.PostgreSQLAsyncReplication.promote_to_primary") as _promote_to_primary,
        patch("charm.Patroni.switchover") as _switchover,
    ):
        event = Mock()
        event.params = {"scope": "cluster"}

        # Cluster
        harness.charm._on_promote_to_primary(event)
        _promote_to_primary.assert_called_once_with(event)

        # Unit, no force, regular promotion
        event.params = {"scope": "unit"}

        harness.charm._on_promote_to_primary(event)

        _switchover.assert_called_once_with("postgresql-0")

        # Unit, no force, switchover failed
        event.params = {"scope": "unit"}
        _switchover.side_effect = SwitchoverFailedError

        harness.charm._on_promote_to_primary(event)

        event.fail.assert_called_once_with(
            "Switchover failed or timed out, check the logs for details"
        )
        event.fail.reset_mock()

        # Unit, no force, not sync
        event.params = {"scope": "unit"}
        _switchover.side_effect = SwitchoverNotSyncError

        harness.charm._on_promote_to_primary(event)

        event.fail.assert_called_once_with("Unit is not sync standby")
        event.fail.reset_mock()

        # Unit, no force, raft stuck
        event.params = {"scope": "unit"}
        rel_id = harness.model.get_relation(PEER).id
        with harness.hooks_disabled():
            harness.update_relation_data(rel_id, harness.charm.unit.name, {"raft_stuck": "True"})

        harness.charm._on_promote_to_primary(event)
        event.fail.assert_called_once_with(
            "Raft is stuck. Set force to reinitialise with new primary"
        )

        # Unit, raft reinit
        event.params = {"scope": "unit", "force": "true"}
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm._on_promote_to_primary(event)
        _raft_reinitialisation.assert_called_once_with()
        assert harness.charm.unit_peer_data["raft_candidate"] == "True"


def test_get_ldap_parameters(harness):
    with (
        patch("charm.PostgreSQLLDAP.get_relation_data") as _get_relation_data,
        patch(
            target="charm.PostgresqlOperatorCharm.is_cluster_initialised",
            new_callable=PropertyMock,
            return_value=True,
        ) as _cluster_initialised,
    ):
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER).id,
                harness.charm.app.name,
                {"ldap_enabled": "False"},
            )

        harness.charm.get_ldap_parameters()
        _get_relation_data.assert_not_called()
        _get_relation_data.reset_mock()

        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER).id,
                harness.charm.app.name,
                {"ldap_enabled": "True"},
            )

        harness.charm.get_ldap_parameters()
        _get_relation_data.assert_called_once()
        _get_relation_data.reset_mock()


def test_handle_processes_failures(harness):
    _now = datetime.now(UTC)
    with (
        patch(
            "charm.Patroni.member_inactive",
            new_callable=PropertyMock,
            return_value=False,
        ) as _member_inactive,
        patch(
            "charm.Patroni.restart_patroni",
        ) as _restart_patroni,
        patch("charm.os.listdir", return_value=["other_dirs", "pg_wal"]) as _listdir,
        patch("charm.os.rename") as _rename,
        patch("charm.datetime") as _datetime,
    ):
        _datetime.now.return_value = _now
        rel_id = harness.model.get_relation(PEER).id
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True", "members_ips": '["192.0.2.0"]'},
            )

        # Does nothing if member is inactive
        assert not harness.charm._handle_processes_failures()
        assert not _restart_patroni.called

        # Will not remove pg_wal if dir look right
        _member_inactive.return_value = True
        assert harness.charm._handle_processes_failures()
        _restart_patroni.assert_called_once_with()
        _restart_patroni.reset_mock()

        # Will move pg_wal if there's only a pg_wal dir
        _listdir.return_value = ["pg_wal"]
        assert harness.charm._handle_processes_failures()
        assert not _restart_patroni.called
        _rename.assert_called_once_with(
            os.path.join(POSTGRESQL_DATA_PATH, "pg_wal"),
            os.path.join(POSTGRESQL_DATA_PATH, f"pg_wal-{_now.isoformat()}"),
        )
        _rename.reset_mock()

        # Will not move pg_wal if there's only a pg_wal dir or other moved dirs
        _listdir.return_value = ["pg_wal", f"pg_wal-{_now.isoformat()}"]
        assert harness.charm._handle_processes_failures()
        _restart_patroni.assert_called_once_with()
        assert not _rename.called
        _restart_patroni.reset_mock()
