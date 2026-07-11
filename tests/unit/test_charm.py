# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import contextlib
import itertools
import json
import logging
import os
import pathlib
import platform
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, PropertyMock, call, patch, sentinel

# from ops.framework import EventBase
import charm_refresh
import psycopg2
import pytest
import tomli
from charmlibs import snap
from ops import (
    ActiveStatus,
    BlockedStatus,
    ErrorStatus,
    JujuVersion,
    MaintenanceStatus,
    ModelError,
    RelationDataTypeError,
    RelationEvent,
    Unit,
    UnknownStatus,
    WaitingStatus,
)
from ops.testing import Harness
from psycopg2 import OperationalError
from single_kernel_postgresql.config.exceptions import (
    NotReadyError,
    RemoveRaftMemberFailedError,
    SwitchoverFailedError,
    SwitchoverNotSyncError,
)
from single_kernel_postgresql.config.literals import PEER_RELATION, SECRET_INTERNAL_LABEL
from single_kernel_postgresql.utils.postgresql import (
    PostgreSQLCreateUserError,
    PostgreSQLEnableDisableExtensionError,
)
from tenacity import RetryError, wait_fixed

from backups import CANNOT_RESTORE_PITR
from charm import (
    EXTENSIONS_DEPENDENCY_MESSAGE,
    PRIMARY_NOT_REACHABLE_MESSAGE,
    PostgresqlOperatorCharm,
    StorageUnavailableError,
)
from constants import (
    POSTGRESQL_DATA_DIR,
    UPDATE_CERTS_BIN_PATH,
)

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"

# used for assert functions


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    harness.add_relation(PEER_RELATION, harness.charm.app.name)
    harness.add_relation("restart", harness.charm.app.name)
    yield harness
    harness.cleanup()


def test_config_fallback(harness):
    """Test that config options with dashes (-) override config options with underscores (_)."""
    harness.disable_hooks()
    charm: PostgresqlOperatorCharm = harness.charm

    assert charm.config.connection_authentication_timeout == 60

    harness.update_config({"connection_authentication_timeout": 50})
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 50

    harness.update_config({"connection-authentication-timeout": 90})
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 90

    harness.update_config(unset=["connection-authentication-timeout"])
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 50

    harness.update_config(unset=["connection_authentication_timeout"])
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 60

    harness.update_config({"connection-authentication-timeout": 120})
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 120


def test_on_install(harness):
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch("charm.PostgresqlOperatorCharm._install_snap_package") as _install_snap_package,
        patch("charm.PostgresqlOperatorCharm._check_detached_storage"),
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            side_effect=[False, True, True],
        ) as _is_storage_attached,
    ):
        pg_snap = _snap_cache.return_value[charm_refresh.snap_name()]

        # Test without adding Patroni resource.
        harness.charm.on.install.emit()
        # Assert that the needed calls were made.
        _install_snap_package.assert_called_once_with(revision=None)
        assert pg_snap.alias.call_count == 2
        pg_snap.alias.assert_any_call("psql")
        pg_snap.alias.assert_any_call("patronictl")

        # Assert the status set by the event handler.
        assert isinstance(harness.model.unit.status, WaitingStatus)


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
        patch(
            "charm.PostgresqlOperatorCharm._peers", new_callable=PropertyMock, return_value=True
        ),
        patch("charm.PatroniManager.get_member_ip", return_value="1.1.1.1") as _get_member_ip,
        patch("charm.PatroniManager.get_primary", return_value=sentinel.primary) as _get_primary,
    ):
        assert harness.charm.primary_endpoint == "1.1.1.1"

        _get_member_ip.assert_called_once_with(sentinel.primary)
        _get_primary.assert_called_once_with()


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
        patch("charm.PatroniManager.get_member_ip", return_value="1.1.1.1") as _get_member_ip,
        patch("charm.PatroniManager.get_primary", return_value=sentinel.primary) as _get_primary,
        # patch("charm.PostgresqlOperatorCharm._patroni", new_callable=PropertyMock) as _patroni,
    ):
        assert harness.charm.primary_endpoint is None

        assert not _get_member_ip.called
        assert not _get_primary.called


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
        patch("charm.PostgresqlOperatorCharm._reconfigure_cluster"),
        patch("charm.TLSManager.generate_internal_peer_cert"),
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
    rel_id = harness.model.get_relation(PEER_RELATION).id
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
            "charm.PostgresqlOperatorCharm.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch(
            "charm.PostgresqlOperatorCharm.is_cluster_initialised", new_callable=PropertyMock
        ) as _is_cluster_initialised,
        patch("charm.PostgresqlOperatorCharm.update_endpoint_addresses"),
        # patch(
        #     "relations.logical_replication.PostgreSQLLogicalReplication.apply_changed_config",
        #     return_value=True,
        # ),
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
        patch("charm.PatroniManager.get_primary") as _get_primary,
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
        del harness.charm.config
        harness.charm.enable_disable_extensions()
        assert isinstance(harness.model.unit.status, BlockedStatus)
        assert harness.model.unit.status.message == EXTENSIONS_DEPENDENCY_MESSAGE


def test_enable_disable_extensions(harness, caplog):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint", new_callable=PropertyMock
        ) as _get_primary,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip"),
        patch.object(harness.charm, "patroni_manager"),
        patch("subprocess.check_output", return_value=b"C"),
        patch.object(harness.charm, "postgresql") as postgresql_mock,
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


@pytest.mark.parametrize("unsettable_status", [ErrorStatus(), UnknownStatus()])
def test_enable_disable_extensions_does_not_restore_unsettable_status(harness, unsettable_status):
    # enable_disable_extensions caches the current unit status and restores it
    # afterwards. The getter can return statuses the backend rejects on set
    # (e.g. "error" or "unknown") left over from a previously failed hook;
    # restoring such a cached status must not be attempted, otherwise the backend
    # raises InvalidStatusError/ModelError and deadlocks the unit.
    with (
        patch("charm.PatroniManager.get_primary") as _get_primary,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip"),
        patch("charm.PostgresqlOperatorCharm._patroni"),
        patch("subprocess.check_output", return_value=b"C"),
        patch.object(harness.charm, "postgresql", Mock()) as postgresql_mock,
    ):
        _get_primary.return_value = harness.charm.unit
        postgresql_mock.enable_disable_extensions.side_effect = None

        # Cache an unsettable status that would otherwise be restored verbatim.
        harness.charm.unit._status = unsettable_status

        # Must not raise when the cached status is restored at the end.
        harness.charm.enable_disable_extensions()

        # The unsettable cached status was skipped, not restored.
        assert not isinstance(harness.charm.unit.status, ErrorStatus | UnknownStatus)


def test_on_start_no_password(harness):
    """Test start is deferred when passwords are not yet generated."""
    with (
        patch("charm.PostgresqlOperatorCharm._check_detached_storage"),
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
    ):
        _get_password.return_value = None
        harness.charm.on.start.emit()
        assert isinstance(harness.model.unit.status, WaitingStatus)

        # ModelError when fetching the password has the same outcome.
        _get_password.side_effect = ModelError
        harness.charm.on.start.emit()
        assert isinstance(harness.model.unit.status, WaitingStatus)


def test_on_start_bootstrap_failure(harness):
    """Test start is blocked when cluster bootstrap fails."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm._restart_services_after_reboot"
        ) as _restart_services_after_reboot,
        patch("charm.PatroniManager.get_primary", return_value=sentinel.primary),
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLApplication.replication_password",
            new_callable=PropertyMock,
        ) as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("charm.PostgresqlOperatorCharm._ensure_storage_layout"),
        patch("charm.PostgresqlOperatorCharm._check_detached_storage"),
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLSManager.generate_internal_peer_cert"),
        patch("charm.PatroniManager.bootstrap_cluster") as _bootstrap_cluster,
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("charm.start_raft_observer"),
    ):
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"
        _bootstrap_cluster.return_value = False

        # TODO: test replicas start (DPE-494).
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.start.emit()
        _bootstrap_cluster.assert_called_once()
        _restart_services_after_reboot.assert_called_once()
        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_on_start_create_user_error(harness):
    """Test start is blocked when creating the default postgres user fails."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm._restart_services_after_reboot"
        ) as _restart_services_after_reboot,
        patch(
            "charm.PostgresqlOperatorCharm._replication_password", new_callable=PropertyMock
        ) as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("charm.PostgresqlOperatorCharm._ensure_storage_layout"),
        patch("charm.PostgresqlOperatorCharm._check_detached_storage"),
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLSManager.generate_internal_peer_cert"),
        patch("charm.PatroniManager.bootstrap_cluster") as _bootstrap_cluster,
        patch(
            "charm.PatroniManager.member_started",
            new_callable=PropertyMock,
        ) as _member_started,
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
        patch.object(harness.charm, "postgresql") as _postgresql,
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("charm.start_raft_observer"),
    ):
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"
        _bootstrap_cluster.return_value = True
        _member_started.return_value = True
        _postgresql.list_users.return_value = []
        _postgresql.create_user.side_effect = PostgreSQLCreateUserError

        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.start.emit()
        _postgresql.create_user.assert_called_once()
        _restart_services_after_reboot.assert_called_once()
        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_on_start_success(harness):
    """Test successful cluster bootstrapping on the primary unit."""
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
        patch("charm.PostgresqlOperatorCharm._update_relation_endpoints"),
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch(
            "charm.PostgresqlOperatorCharm._replication_password",
            new_callable=PropertyMock,
        ) as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("charm.PostgresqlOperatorCharm._ensure_storage_layout"),
        patch("charm.PostgresqlOperatorCharm._check_detached_storage"),
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLSManager.generate_internal_peer_cert"),
        patch("charm.PatroniManager.bootstrap_cluster") as _bootstrap_cluster,
        patch(
            "charm.PatroniManager.member_started",
            new_callable=PropertyMock,
        ) as _member_started,
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
        patch.object(harness.charm, "postgresql") as _postgresql,
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("charm.start_raft_observer"),
    ):
        _get_password.return_value = "fake-operator-password"
        _replication_password.return_value = "fake-replication-password"
        _bootstrap_cluster.return_value = True
        _member_started.return_value = True
        _postgresql.list_users.return_value = []

        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.start.emit()
        assert _postgresql.create_user.call_count == 2  # backup user + monitoring user
        _oversee_users.assert_called_once()
        _enable_disable_extensions.assert_called_once()
        _set_primary_status_message.assert_called_once()
        _restart_services_after_reboot.assert_called_once()


def test_setup_users_skips_writes_on_standby_cluster(harness):
    """A standby cluster is read-only, so _setup_users must not issue write DDL (DPE-10284)."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_cluster",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
    ):
        # Even though this would raise on a read-only standby, the guard must
        # short-circuit before create_predefined_instance_roles is ever called.
        _postgresql.create_predefined_instance_roles.side_effect = (
            psycopg2.errors.ReadOnlySqlTransaction
        )

        harness.charm._setup_users()

        _postgresql.create_predefined_instance_roles.assert_not_called()
        _postgresql.create_user.assert_not_called()
        _postgresql.set_up_database.assert_not_called()
        _oversee_users.assert_not_called()


def test_setup_users_runs_on_primary_cluster(harness):
    """On a primary cluster _setup_users provisions the predefined roles and users."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_cluster",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch.object(harness.charm, "postgresql") as _postgresql,
    ):
        _postgresql.list_users.return_value = []
        _postgresql.list_access_groups.return_value = set()

        harness.charm._setup_users()

        _postgresql.create_predefined_instance_roles.assert_called_once()
        assert _postgresql.create_user.call_count == 2  # backup user + monitoring user
        _oversee_users.assert_called_once()


def test_is_standby_cluster(harness):
    """is_standby_cluster is relation-based and independent of the Patroni API."""
    with (
        patch("charm.PostgreSQLAsyncReplication.is_primary_cluster") as _is_primary_cluster,
        patch.object(harness.charm.model, "get_relation") as _get_relation,
    ):
        # No replication relation at all -> not a standby cluster.
        _get_relation.return_value = None
        assert harness.charm.is_standby_cluster is False

        # Replication relation present, and this app is the primary cluster.
        _get_relation.return_value = Mock()
        _is_primary_cluster.return_value = True
        assert harness.charm.is_standby_cluster is False

        # Replication relation present, and this app is NOT the primary cluster -> standby.
        _is_primary_cluster.return_value = False
        assert harness.charm.is_standby_cluster is True


def test_on_start_replica(harness):
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch(
            "single_kernel_postgresql.workload.base.BaseWorkload.get_postgresql_version"
        ) as _get_postgresql_version,
        patch(
            "charm.PostgresqlOperatorCharm._restart_services_after_reboot"
        ) as _restart_services_after_reboot,
        patch("charm.PatroniManager.configure_patroni_on_unit") as _configure_patroni_on_unit,
        patch(
            "charm.PatroniManager.member_started",
            new_callable=PropertyMock,
        ) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm._update_relation_endpoints", new_callable=PropertyMock
        ) as _update_relation_endpoints,
        patch("ops.framework.EventBase.defer") as _defer,
        patch("charm.PostgresqlOperatorCharm._replication_password") as _replication_password,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("charm.PostgresqlOperatorCharm._ensure_storage_layout"),
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached",
            return_value=True,
        ) as _is_storage_attached,
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLSManager.generate_internal_peer_cert"),
        patch("charm.start_raft_observer"),
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
        patch(
            "single_kernel_postgresql.workload.base.BaseWorkload.get_postgresql_version"
        ) as _get_postgresql_version,
        patch.object(harness.charm, "postgresql") as _postgresql,
        patch.object(harness.charm, "patroni_manager") as _patroni_manager,
        patch("charm.PostgresqlOperatorCharm._get_password") as _get_password,
        patch("charm.PostgresqlOperatorCharm._ensure_storage_layout"),
        patch(
            "charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True
        ) as _is_storage_attached,
        patch("charm.PostgresqlOperatorCharm.get_secret"),
        patch("charm.TLSManager.generate_internal_peer_cert"),
        patch("charm.PostgreSQLProvider.get_username_mapping", return_value={}),
        patch("charm.PostgreSQLProvider.get_databases_prefix_mapping", return_value={}),
        patch("charm.start_raft_observer"),
    ):
        # Mock the passwords.
        _get_postgresql_version.return_value = "16.6"
        _patroni_manager.member_started = False
        _get_password.return_value = "fake-operator-password"
        bootstrap_cluster = _patroni_manager.bootstrap_cluster
        bootstrap_cluster.return_value = True

        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.start.emit()
        bootstrap_cluster.assert_called_once()
        _postgresql.create_user.assert_not_called()
        assert isinstance(harness.model.unit.status, WaitingStatus)
        assert harness.model.unit.status.message == "awaiting for member to start"


def test_on_start_after_blocked_state(harness):
    with (
        patch("charm.PatroniManager.bootstrap_cluster") as _bootstrap_cluster,
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


def test_ensure_storage_layout(harness, tmp_path):
    """_ensure_storage_layout creates TEMP_DATA_DIR and chowns the versioned parents.

    Data migration between storage roots and versioned paths is handled
    by the snap hooks.  The charm ensures TEMP_DATA_DIR exists (it may live
    on a tmpfs mount that is wiped on reboot) and makes the 16/ parent of
    both the temp and the data dirs _daemon_-owned, so Patroni can remove
    the versioned subdirectory (e.g. during patronictl reinit).
    """
    temp_root = tmp_path / "temp" / "16" / "main"
    data_root = tmp_path / "data" / "16" / "main"
    # The snap's migrate-data.sh creates the data parent (as root); the
    # charm only fixes its ownership, it does not create the data dir.
    data_root.parent.mkdir(parents=True)
    with (
        patch("charm.TEMP_DATA_DIR", str(temp_root)),
        patch("charm.POSTGRESQL_DATA_DIR", str(data_root)),
        patch("charm.shutil") as mock_shutil,
    ):
        harness.charm._ensure_storage_layout()
    assert temp_root.is_dir()
    # The charm does not create the data dir leaf — only the snap does.
    assert not data_root.exists()
    # Both versioned 16/ parents (and the temp leaf) are chowned to _daemon_.
    chowned = {call.args[0] for call in mock_shutil.chown.call_args_list}
    assert temp_root in chowned
    assert temp_root.parent in chowned
    assert data_root.parent in chowned
    assert not (tmp_path / "archive").exists()
    assert not (tmp_path / "logs").exists()


def test_migrate_temp_tablespace_location_skips_when_not_primary(harness):
    """If the unit is not primary, the migration is skipped."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm.is_primary",
            new_callable=PropertyMock,
            return_value=False,
        ),
    ):
        result = harness.charm._migrate_temp_tablespace_location()

    assert result is True


def test_migrate_temp_tablespace_location_skips_when_no_endpoint(harness):
    """If primary_endpoint is not yet set, the migration is skipped."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm.is_primary",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value=None,
        ),
    ):
        result = harness.charm._migrate_temp_tablespace_location()

    assert result is True


def test_migrate_temp_tablespace_location_migrates_from_old_path(harness, tmp_path):
    """When temp tablespace is at old TEMP_STORAGE_PATH, it is migrated to TEMP_DATA_DIR."""
    temp_data_dir = tmp_path / "temp" / "16" / "main"
    temp_storage_path = str(tmp_path / "temp")
    temp_data_dir.mkdir(parents=True)

    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = (temp_storage_path,)  # still at old path
    connection.cursor.return_value = cursor
    postgresql = MagicMock()
    postgresql._connect_to_database.return_value = connection

    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value="10.0.0.1",
        ),
        patch.object(harness.charm, "_resolve_primary_host", return_value="10.0.0.1"),
        patch(
            "charm.PostgresqlOperatorCharm.postgresql",
            new_callable=PropertyMock,
            return_value=postgresql,
        ),
        patch("charm.TEMP_DATA_DIR", str(temp_data_dir)),
        patch("charm.TEMP_STORAGE_PATH", temp_storage_path),
    ):
        assert harness.charm._migrate_temp_tablespace_location()

    cursor.execute.assert_has_calls([
        call("SELECT pg_tablespace_location(oid) FROM pg_tablespace WHERE spcname='temp';"),
        call("DROP TABLESPACE temp;"),
        call(f"CREATE TABLESPACE temp LOCATION '{temp_data_dir}';"),
        call("GRANT CREATE ON TABLESPACE temp TO public;"),
    ])


def test_migrate_temp_tablespace_location_skips_when_already_at_versioned_path(harness, tmp_path):
    """When temp tablespace is already at TEMP_DATA_DIR, no migration is performed."""
    temp_data_dir = tmp_path / "temp" / "16" / "main"
    temp_data_dir.mkdir(parents=True)

    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = (str(temp_data_dir),)  # already at versioned path
    connection.cursor.return_value = cursor
    postgresql = MagicMock()
    postgresql._connect_to_database.return_value = connection

    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value="10.0.0.1",
        ),
        patch.object(harness.charm, "_resolve_primary_host", return_value="10.0.0.1"),
        patch(
            "charm.PostgresqlOperatorCharm.postgresql",
            new_callable=PropertyMock,
            return_value=postgresql,
        ),
        patch("charm.TEMP_DATA_DIR", str(temp_data_dir)),
    ):
        assert harness.charm._migrate_temp_tablespace_location()

    # Only the SELECT should have been executed — no DROP/CREATE
    cursor.execute.assert_called_once_with(
        "SELECT pg_tablespace_location(oid) FROM pg_tablespace WHERE spcname='temp';"
    )


def test_migrate_temp_tablespace_location_skips_when_tablespace_missing(harness, tmp_path):
    """When the tablespace doesn't exist in pg_catalog, no migration is needed."""
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # tablespace was never created or already dropped
    connection.cursor.return_value = cursor
    postgresql = MagicMock()
    postgresql._connect_to_database.return_value = connection

    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value="10.0.0.1",
        ),
        patch.object(harness.charm, "_resolve_primary_host", return_value="10.0.0.1"),
        patch(
            "charm.PostgresqlOperatorCharm.postgresql",
            new_callable=PropertyMock,
            return_value=postgresql,
        ),
    ):
        assert harness.charm._migrate_temp_tablespace_location()

    # Only the SELECT should have been executed
    cursor.execute.assert_called_once_with(
        "SELECT pg_tablespace_location(oid) FROM pg_tablespace WHERE spcname='temp';"
    )


def test_migrate_temp_tablespace_location_skips_when_unexpected_location(harness, tmp_path):
    """When the tablespace is at an unexpected location, migration is skipped with a warning."""
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = ("/some/unexpected/path",)
    connection.cursor.return_value = cursor
    postgresql = MagicMock()
    postgresql._connect_to_database.return_value = connection

    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value="10.0.0.1",
        ),
        patch.object(harness.charm, "_resolve_primary_host", return_value="10.0.0.1"),
        patch(
            "charm.PostgresqlOperatorCharm.postgresql",
            new_callable=PropertyMock,
            return_value=postgresql,
        ),
        patch("charm.logger") as logger,
    ):
        assert harness.charm._migrate_temp_tablespace_location()

    cursor.execute.assert_called_once_with(
        "SELECT pg_tablespace_location(oid) FROM pg_tablespace WHERE spcname='temp';"
    )
    logger.warning.assert_called_once()


def test_migrate_temp_tablespace_location_returns_false_on_db_error(harness):
    """When a psycopg2 error occurs, the method returns False."""
    postgresql = MagicMock()
    postgresql._connect_to_database.side_effect = psycopg2.Error("connection failed")

    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
            return_value="10.0.0.1",
        ),
        patch.object(harness.charm, "_resolve_primary_host", return_value="10.0.0.1"),
        patch(
            "charm.PostgresqlOperatorCharm.postgresql",
            new_callable=PropertyMock,
            return_value=postgresql,
        ),
    ):
        assert not harness.charm._migrate_temp_tablespace_location()


def test_ensure_storage_layout_recreates_temp_dir_on_reboot(harness, tmp_path):
    """TEMP_DATA_DIR is recreated after a tmpfs wipe on reboot."""
    temp_root = tmp_path / "temp" / "16" / "main"
    with (
        patch("charm.TEMP_DATA_DIR", str(temp_root)),
        patch("charm.shutil"),
    ):
        harness.charm._ensure_storage_layout()
    assert temp_root.is_dir()


def test_on_update_status(harness):
    with (
        patch("charm.ClusterTopologyObserver.start_observer") as _start_observer,
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
        patch("charm.PatroniManager.restart_patroni") as _restart_patroni,
        patch("charm.PatroniManager.is_member_isolated") as _is_member_isolated,
        patch("charm.PatroniManager.member_started", new_callable=PropertyMock) as _member_started,
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
        patch("charm.PatroniManager.last_postgresql_logs") as _last_postgresql_logs,
        patch("charm.PatroniManager.patroni_logs") as _patroni_logs,
        patch("charm.PatroniManager.get_member_status") as _get_member_status,
        patch(
            "charm.PostgreSQLBackups.can_use_s3_repository", return_value=(True, None)
        ) as _can_use_s3_repository,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm.log_pitr_last_transaction_time"),
        patch("charm.PostgreSQL.drop_hba_triggers") as _drop_hba_triggers,
    ):
        rel_id = harness.model.get_relation(PEER_RELATION).id
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
        _start_observer.reset_mock()
        _member_started.return_value = False
        _is_member_isolated.return_value = True
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.unit.name, {"postgresql_restarted": ""}
            )
        harness.charm.on.update_status.emit()
        _restart_patroni.assert_called_once()
        _start_observer.assert_called_once_with()
        _drop_hba_triggers.assert_called_once_with()


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
            new_callable=PropertyMock,
            return_value=True,
        ) as _primary_endpoint,
        patch("charm.PostgreSQLProvider.oversee_users") as _oversee_users,
        patch(
            "charm.PostgresqlOperatorCharm._handle_processes_failures"
        ) as _handle_processes_failures,
        patch("charm.PostgreSQLBackups.can_use_s3_repository") as _can_use_s3_repository,
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.get_current_timeline"
        ) as _get_current_timeline,
        patch("charm.PostgresqlOperatorCharm._setup_users") as _setup_users,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PatroniManager.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.PatroniManager.get_member_status") as _get_member_status,
        patch(
            "charm.PostgresqlOperatorCharm.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch("charm.PostgreSQL.drop_hba_triggers") as _drop_hba_triggers,
    ):
        _get_current_timeline.return_value = "2"
        rel_id = harness.model.get_relation(PEER_RELATION).id
        # Test when the restore operation fails.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {
                    "cluster_initialised": "True",
                    "restoring-backup": "20230101-090000F",
                    "refresh_remove_trigger": "True",
                },
            )
        _get_member_status.return_value = "failed"
        harness.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _oversee_users.assert_not_called()
        _update_relation_endpoints.assert_not_called()
        _set_primary_status_message.assert_not_called()
        _enable_disable_extensions.assert_not_called()
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
        _enable_disable_extensions.assert_not_called()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is still in the application relation databag.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True",
            "restoring-backup": "20230101-090000F",
            "refresh_remove_trigger": "True",
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
        _enable_disable_extensions.assert_called_once_with()
        assert isinstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is not in the application relation databag anymore.
        assert harness.get_relation_data(rel_id, harness.charm.app) == {
            "cluster_initialised": "True",
            "refresh_remove_trigger": "True",
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
            "refresh_remove_trigger": "True",
        }
        assert not _drop_hba_triggers.called


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


def test_check_detached_storage(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._is_storage_attached") as _is_storage_attached,
        patch("charm.wait_fixed", return_value=wait_fixed(0)),
    ):
        _is_storage_attached.return_value = False
        with pytest.raises(StorageUnavailableError):
            harness.charm._check_detached_storage()
        assert isinstance(harness.charm.unit.status, WaitingStatus)


@pytest.mark.parametrize("unsettable_status", [ErrorStatus(), UnknownStatus()])
def test_check_detached_storage_does_not_restore_unsettable_status(harness, unsettable_status):
    # The unit.status getter can return statuses that the juju backend rejects
    # on set (e.g. "error" or "unknown"). Restoring such a cached status must not
    # be attempted, otherwise the backend raises InvalidStatusError/ModelError.
    harness.charm.unit._status = unsettable_status
    with patch("charm.PostgresqlOperatorCharm._is_storage_attached", return_value=True):
        # Storage is attached, so the cached status would be restored verbatim;
        # this must not raise.
        harness.charm._check_detached_storage()


def test_restart(harness):
    with (
        patch("charm.PatroniManager.restart_postgresql") as _restart_postgresql,
        patch("charm.PatroniManager.are_all_members_ready") as _are_all_members_ready,
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


def test_request_restart(harness):
    """Bridge for ConfigManager: clears the restarted flag and acquires the restart lock."""
    with patch(
        "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
    ) as _on_acquire_lock:
        rel_id = harness.model.get_relation(PEER_RELATION).id
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.unit.name, {"postgresql_restarted": "True"}
            )

        harness.charm.request_restart()

        assert "postgresql_restarted" not in harness.charm.unit_peer_data
        _on_acquire_lock.assert_called_once()


def test_refresh_endpoints(harness):
    """Bridge for ConfigManager: refreshes the client-relation endpoints."""
    with patch("charm.PostgreSQLProvider.update_endpoints") as _update_endpoints:
        harness.charm.refresh_endpoints()

        _update_endpoints.assert_called_once()


def test_restart_services(harness):
    """Bridge for ConfigManager: restarts the metrics and LDAP sync snap services."""
    with (
        patch("charm.snap.SnapCache") as _snap_cache,
        patch(
            "charm.PostgresqlOperatorCharm._restart_metrics_service"
        ) as _restart_metrics_service,
        patch(
            "charm.PostgresqlOperatorCharm._restart_ldap_sync_service"
        ) as _restart_ldap_sync_service,
    ):
        postgres_snap = _snap_cache.return_value.__getitem__.return_value

        harness.charm.restart_services()

        _restart_metrics_service.assert_called_once_with(postgres_snap)
        _restart_ldap_sync_service.assert_called_once_with(postgres_snap)


def test_update_config_delegates_to_config_manager(harness):
    """update_config forwards to the lib's ConfigManager with the charm-owned user hash/refresh."""
    with patch.object(harness.charm, "config_manager") as _config_manager:
        _config_manager.update_config.return_value = True

        result = harness.charm.update_config(no_peers=True)

        assert result is True
        _config_manager.update_config.assert_called_once()
        args, kwargs = _config_manager.update_config.call_args
        assert args[1] == harness.charm.generate_user_hash
        assert kwargs["no_peers"] is True
        assert kwargs["refresh"] is harness.charm.refresh


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
        patch(
            "charm.PostgresqlOperatorCharm._set_primary_status_message"
        ) as _set_primary_status_message,
    ):
        harness.model.unit.status = WaitingStatus(PRIMARY_NOT_REACHABLE_MESSAGE)

        harness.charm._on_cluster_topology_change(Mock())

        _update_relation_endpoints.assert_called_once_with()
        _primary_endpoint.assert_called_once_with()
        _set_primary_status_message.assert_called_once_with()


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
        del harness.charm.config

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert str(e.value) == "ldap_map config option has an invalid value"

        _charm_lib.return_value.validate_group_map.assert_called_once_with("ldap_group=")
        _charm_lib.return_value.validate_group_map.return_value = True

        # Test request_date_style exception
        with harness.hooks_disabled():
            harness.update_config({"request_date_style": "ISO, TEST"})
        del harness.charm.config

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert str(e.value) == "request_date_style config option has an invalid value"

        _charm_lib.return_value.validate_date_style.assert_called_once_with("ISO, TEST")
        _charm_lib.return_value.validate_date_style.return_value = True

        # Test request_time_zone exception
        with harness.hooks_disabled():
            harness.update_config({"request_time_zone": "TEST_ZONE"})
        del harness.charm.config

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        assert str(e.value) == "request_time_zone config option has an invalid value"

        _charm_lib.return_value.get_postgresql_timezones.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_timezones.return_value = ["TEST_ZONE"]

        # Test locales exception
        with harness.hooks_disabled():
            harness.update_config({"response_lc_monetary": "test_TEST"})
        del harness.charm.config

        with pytest.raises(ValueError) as e:
            harness.charm._validate_config_options()
        message = "1 validation error for CharmConfig\nresponse_lc_monetary\n  Input should be "
        assert str(e.value).startswith(message)


def test_config_validation_invalid_worker_values(harness):
    """Test that pydantic validates worker process config values."""
    # Test invalid string value (not "auto" or a number)
    with harness.hooks_disabled():
        harness.update_config({"cpu-max-worker-processes": "invalid"})
    with contextlib.suppress(AttributeError):
        del harness.charm.config

    with pytest.raises(ValueError) as e:
        _ = harness.charm.config

    # Pydantic should reject this
    assert "validation error" in str(e.value).lower()

    # Test negative number
    with harness.hooks_disabled():
        harness.update_config({"cpu-max-worker-processes": "-5"})
    with contextlib.suppress(AttributeError):
        del harness.charm.config

    with pytest.raises(ValueError) as e:
        _ = harness.charm.config

    # Pydantic should reject this
    assert "validation error" in str(e.value).lower()

    # Test value less than 2 - should be accepted at config level but fail during calculation
    with harness.hooks_disabled():
        harness.update_config({"cpu-max-worker-processes": "2", "cpu-max-parallel-workers": "7"})
    with contextlib.suppress(AttributeError):
        del harness.charm.config

    # The config should accept it (as it gets validated later in the calculation method)
    assert harness.charm.config.cpu_max_parallel_workers == 7


def test_config_validation_valid_worker_values(harness):
    """Test that pydantic accepts valid worker process config values."""
    with harness.hooks_disabled():
        # Test "auto"
        harness.update_config({"cpu-max-worker-processes": "auto"})
        with contextlib.suppress(AttributeError):
            del harness.charm.config
        assert harness.charm.config.cpu_max_worker_processes == "auto"

        # Test positive integer
        harness.update_config({"cpu-max-worker-processes": "16"})
        with contextlib.suppress(AttributeError):
            del harness.charm.config
        assert harness.charm.config.cpu_max_worker_processes == 16

        # Test large positive integer
        harness.update_config({"cpu-max-parallel-workers": "1000"})
        with contextlib.suppress(AttributeError):
            del harness.charm.config
        assert harness.charm.config.cpu_max_parallel_workers == 1000


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
        patch("charm.PostgresqlOperatorCharm.is_standby_leader") as _is_standby_leader,
        patch("charm.PostgresqlOperatorCharm.is_primary") as _is_primary,
        patch("charm.PatroniManager.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.PatroniManager.start_patroni") as _start_patroni,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._update_member_ip") as _update_member_ip,
        patch("charm.PostgresqlOperatorCharm._reconfigure_cluster") as _reconfigure_cluster,
        patch("ops.framework.EventBase.defer") as _defer,
    ):
        rel_id = harness.model.get_relation(PEER_RELATION).id
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
        relation = harness.model.get_relation(PEER_RELATION, rel_id)
        _member_started.return_value = True
        for values in [True, False]:
            _defer.reset_mock()
            _start_stop_pgbackrest_service.reset_mock()
            _is_primary.return_value = values
            _is_standby_leader.return_value = values
            harness.charm.unit.status = ActiveStatus()
            harness.charm.on.database_peers_relation_changed.emit(relation)
            if _is_primary.return_value == values:
                _defer.assert_not_called()
                _start_stop_pgbackrest_service.assert_called_once()
                assert isinstance(harness.charm.unit.status, ActiveStatus)
            else:
                _defer.assert_called_once()
                _start_stop_pgbackrest_service.assert_not_called()
                assert isinstance(harness.charm.unit.status, MaintenanceStatus)

        # Test when it was not possible to start the pgBackRest service yet.
        relation = harness.model.get_relation(PEER_RELATION, rel_id)
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
            "charm.PostgresqlOperatorCharm.is_cluster_initialised",
            new_callable=PropertyMock,
            return_value=False,
        ) as _is_cluster_initialised,
        patch("charm.Patroni.cleanup_raft_cluster", return_value=False) as _cleanup_raft_cluster,
    ):
        # Cluster not initialised
        mock_event = MagicMock(spec=RelationEvent)
        mock_event.unit = harness.charm.unit
        mock_event.relation = Mock()

        assert harness.charm._reconfigure_cluster(mock_event)

        assert not _cleanup_raft_cluster.called
        _add_members.assert_called_once_with(mock_event)
        _add_members.reset_mock()

        # Removing members failed
        _is_cluster_initialised.return_value = True

        assert not harness.charm._reconfigure_cluster(mock_event)

        _cleanup_raft_cluster.assert_called_once_with()
        assert not _add_members.called
        _cleanup_raft_cluster.reset_mock()
        _add_members.reset_mock()

        # Happy scenario
        _cleanup_raft_cluster.return_value = True

        assert harness.charm._reconfigure_cluster(mock_event)

        _cleanup_raft_cluster.assert_called_once_with()
        _add_members.assert_called_once_with(mock_event)


def test_update_certificate(harness):
    with (
        patch("charm.TLSManager.get_client_tls_files") as _get_client_tls_files,
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
        patch("charm.PatroniManager.stop_patroni") as _stop_patroni,
        patch("charm.PostgresqlOperatorCharm.update_endpoint_addresses"),
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(harness.charm.watcher_offer, "update_unit_address"),
        patch.object(harness.charm.watcher_offer, "update_endpoints"),
    ):
        rel_id = harness.model.get_relation(PEER_RELATION).id
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
        patch("charm.PatroniManager.are_all_members_ready") as _are_all_members_ready,
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
    rel_id = harness.model.get_relation(PEER_RELATION).id
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
    rel_id = harness.model.get_relation(PEER_RELATION).id

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
    rel_id = harness.model.get_relation(PEER_RELATION).id

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
                    f"{PEER_RELATION}-address": "192.0.2.0",
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
    rel_id = harness.model.get_relation(PEER_RELATION).id

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
        patch("charm.PatroniManager.start_patroni"),
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
        rel_id = harness.model.get_relation(PEER_RELATION).id

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
        assert harness.charm.model.get_secret(label=f"{PEER_RELATION}.postgresql.{scope}")
        assert harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert SECRET_INTERNAL_LABEL not in harness.get_relation_data(
            rel_id, getattr(harness.charm, scope).name
        )


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
        patch("charm.PatroniManager.are_all_members_ready") as _are_all_members_ready,
        patch("charm.PostgresqlOperatorCharm._get_ips_to_remove") as _get_ips_to_remove,
        patch(
            "charm.PostgresqlOperatorCharm.updated_synchronous_node_count"
        ) as _updated_synchronous_node_count,
        patch("charm.Patroni.remove_raft_member") as _remove_raft_member,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip") as _unit_ip,
        patch("charm.PatroniManager.get_member_ip") as _get_member_ip,
    ):
        rel_id = harness.model.get_relation(PEER_RELATION).id
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        _remove_raft_member.assert_called_once_with(f"{mock_ip_address}:2222")
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
        patch("charm.PatroniManager.get_running_cluster_members", return_value=["test"]),
        patch("charm.PatroniManager.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_leader", new_callable=PropertyMock
        ) as _is_standby_leader,
        patch("charm.PatroniManager.get_primary") as _get_primary,
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
        patch(
            "charm.PatroniManager.update_patroni_restart_condition"
        ) as _update_restart_condition,
        patch("charm.PatroniManager.get_patroni_restart_condition") as _get_restart_condition,
        patch("single_kernel_postgresql.core.state.CharmState.unit_ip") as _unit_ip,
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
        patch("charm.PatroniManager.start_patroni") as _start_patroni,
        patch(
            "single_kernel_postgresql.core.state.CharmState.unit_ip",
            new_callable=PropertyMock(return_value="1.1.1.1"),
        ) as _unit_ip,
    ):
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER_RELATION).id,
                harness.charm.app.name,
                {"members_ips": json.dumps([])},
            )
        harness.charm._restart_services_after_reboot()
        _start_patroni.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER_RELATION).id,
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
        del harness.charm.config
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
        del harness.charm.config
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
        patch("charm.PatroniManager.switchover") as _switchover,
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
        rel_id = harness.model.get_relation(PEER_RELATION).id
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
                harness.model.get_relation(PEER_RELATION).id,
                harness.charm.app.name,
                {"ldap_enabled": "False"},
            )

        harness.charm.get_ldap_parameters()
        _get_relation_data.assert_not_called()
        _get_relation_data.reset_mock()

        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER_RELATION).id,
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
            "charm.PatroniManager.member_inactive",
            new_callable=PropertyMock,
            return_value=False,
        ) as _member_inactive,
        patch(
            "charm.PatroniManager.restart_patroni",
        ) as _restart_patroni,
        patch("charm.os.listdir", return_value=["other_dirs", "pg_wal"]) as _listdir,
        patch("charm.os.path.exists", return_value=True) as _exists,
        patch("charm.os.rename") as _rename,
        patch("charm.datetime") as _datetime,
    ):
        _datetime.now.return_value = _now
        rel_id = harness.model.get_relation(PEER_RELATION).id
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
            os.path.join(POSTGRESQL_DATA_DIR, "pg_wal"),
            os.path.join(POSTGRESQL_DATA_DIR, f"pg_wal-{_now.isoformat()}"),
        )
        _rename.reset_mock()

        # Will not move pg_wal if there's only a pg_wal dir or other moved dirs
        _listdir.return_value = ["pg_wal", f"pg_wal-{_now.isoformat()}"]
        assert harness.charm._handle_processes_failures()
        _restart_patroni.assert_called_once_with()
        assert not _rename.called
        _restart_patroni.reset_mock()

        # Does nothing if the data directory does not exist yet (member not bootstrapped)
        _listdir.reset_mock()
        _exists.return_value = False
        assert not harness.charm._handle_processes_failures()
        assert not _restart_patroni.called
        assert not _rename.called
        assert not _listdir.called
        _exists.return_value = True


def test_on_databases_change(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
    ):
        harness.charm._on_databases_change(Mock())

        _update_config.assert_called_once_with()
        assert "timestamp" in harness.charm.unit_peer_data


def test_generate_user_hash(harness):
    with harness.hooks_disabled():
        rel_id = harness.add_relation("database", "application")
        harness.update_relation_data(rel_id, "application", {"database": "test_db"})
    with (
        patch("charm.shake_128") as _shake_128,
    ):
        _shake_128.return_value.hexdigest.return_value = sentinel.hash

        assert harness.charm.generate_user_hash == sentinel.hash

        _shake_128.assert_called_once_with(b"{'relation-2': 'test_db'}")


def test_relations_user_databases_map(harness):
    with (
        patch.object(harness.charm, "postgresql") as _postgresql,
        patch("charm.PatroniManager.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.is_cluster_initialised", new_callable=PropertyMock
        ) as _is_cluster_initialised,
    ):
        # Initial empty results from the functions used in the property that's being tested.
        _postgresql.list_users_from_relation.return_value = set()
        _postgresql.list_accessible_databases_for_user.return_value = set()
        _postgresql.list_access_groups.return_value = {
            "identity_access",
            "internal_access",
            "relation_access",
        }

        # Test when the cluster isn't initialised yet.
        _is_cluster_initialised.return_value = False
        _member_started.return_value = True
        assert harness.charm.relations_user_databases_map == {
            "operator": "all",
            "replication": "all",
            "rewind": "all",
        }

        # Test when the cluster is initialised but the cluster member hasn't started yet.
        _is_cluster_initialised.return_value = True
        _member_started.return_value = False
        assert harness.charm.relations_user_databases_map == {
            "operator": "all",
            "replication": "all",
            "rewind": "all",
        }

        # Test when there are no relation users in the database.
        _member_started.return_value = True
        assert harness.charm.relations_user_databases_map == {}

        # Test when there are relation users in the database.
        _postgresql.list_users.return_value = ["user1", "user2"]
        _postgresql.list_accessible_databases_for_user.side_effect = [["db1", "db2"], ["db3"]]
        assert harness.charm.relations_user_databases_map == {"user1": "db1,db2", "user2": "db3"}

        # Test when the access groups where not created yet.
        _postgresql.list_accessible_databases_for_user.side_effect = [["db1", "db2"], ["db3"]]
        _postgresql.list_access_groups.return_value = set()
        assert harness.charm.relations_user_databases_map == {
            "user1": "db1,db2",
            "user2": "db3",
            "operator": "all",
            "replication": "all",
            "rewind": "all",
        }


def test_on_secret_remove(harness):
    with (
        patch("ops.model.Model.juju_version", new_callable=PropertyMock) as _juju_version,
    ):
        event = Mock()

        # New juju
        _juju_version.return_value = JujuVersion("3.6.11")
        harness.charm._on_secret_remove(event)
        event.remove_revision.assert_called_once_with()
        event.reset_mock()

        # Old juju
        _juju_version.return_value = JujuVersion("3.6.9")
        harness.charm._on_secret_remove(event)
        assert not event.remove_revision.called
        event.reset_mock()

        # No secret
        event.secret.label = None
        harness.charm._on_secret_remove(event)
        assert not event.remove_revision.called
