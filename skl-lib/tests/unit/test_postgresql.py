# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
# ruff: noqa: I001
from unittest.mock import call, patch, sentinel
from datetime import datetime, timezone, UTC

import psycopg2
import pytest
from ops.testing import Harness
from psycopg2.sql import Composed, Identifier, Literal, SQL

from single_kernel_postgresql.charms import k8s_charm, vm_charm
from single_kernel_postgresql.config.literals import (
    PEER_RELATION,
    POSTGRESQL_STORAGE_PERMISSIONS,
    SNAP_USER,
    SYSTEM_USERS,
)
from single_kernel_postgresql.utils.postgresql import (
    ACCESS_GROUPS,
    ACCESS_GROUP_INTERNAL,
    PostgreSQL,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDatabasesSetupError,
    PostgreSQLGetLastArchivedWALError,
    PostgreSQLListDatabasesError,
    PostgreSQLUndefinedHostError,
    PostgreSQLUndefinedPasswordError,
    PostgreSQLUpdateUserError,
    ROLE_DATABASES_OWNER,
)
from single_kernel_postgresql.config.enums import Substrates


@pytest.fixture(autouse=True)
def harness(substrate, test_charm_path):
    with open(test_charm_path + "/metadata.yaml") as meta_file:
        meta = meta_file.read()
    if substrate == "vm":
        harness = Harness(vm_charm.PostgreSQLVMCharm, meta=meta)
    else:
        harness = Harness(k8s_charm.PostgreSQLK8sCharm, meta=meta)

    # Set up the initial relation and hooks.
    peer_rel_id = harness.add_relation(PEER_RELATION, "postgresql-single-kernel")
    harness.add_relation_unit(peer_rel_id, "postgresql-single-kernel/0")
    harness.begin()
    yield harness
    harness.cleanup()


@pytest.mark.parametrize("users_exist", [True, False])
def test_create_access_groups(harness, users_exist):
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            True if users_exist else None
        )
        harness.charm.postgresql.create_access_groups()
        calls = [
            *(
                call(
                    Composed([
                        SQL("SELECT TRUE FROM pg_roles WHERE rolname="),
                        Literal(group),
                        SQL(";"),
                    ])
                )
                for group in ACCESS_GROUPS
            )
        ]
        if not users_exist:
            index = 1
            for group in ACCESS_GROUPS:
                calls.insert(index, call(SQL("CREATE ROLE {} NOLOGIN;").format(Identifier(group))))
                index += 2
        execute.assert_has_calls(calls)


def test_create_database(harness):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
    ):
        # Test a successful database creation.
        database = "test_database"
        plugins = ["test_plugin_1", "test_plugin_2"]
        with harness.hooks_disabled():
            rel_id = harness.add_relation("database", "application")
            harness.add_relation_unit(rel_id, "application/0")
            harness.update_relation_data(rel_id, "application", {"database": database})
        schemas = [("test_schema_1",), ("test_schema_2",)]
        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = schemas
        harness.charm.postgresql.create_database(database, plugins)
        execute = _connect_to_database.return_value.cursor.return_value.execute
        execute.assert_has_calls([
            call(
                Composed([
                    SQL("SELECT datname FROM pg_database WHERE datname="),
                    Literal("test_database"),
                    SQL(";"),
                ])
            )
        ])
        _enable_disable_extensions.assert_called_once_with(
            {plugins[0]: True, plugins[1]: True}, database
        )

        # Test when two relations request the same database.
        _connect_to_database.reset_mock()
        with harness.hooks_disabled():
            other_rel_id = harness.add_relation("database", "other-application")
            harness.add_relation_unit(other_rel_id, "other-application/0")
            harness.update_relation_data(other_rel_id, "other-application", {"database": database})
        harness.charm.postgresql.create_database(database, plugins)

        # Test a failed database creation.
        _enable_disable_extensions.reset_mock()
        execute.side_effect = psycopg2.Error
        try:
            harness.charm.postgresql.create_database(database, plugins)
            assert False
        except PostgreSQLCreateDatabaseError:
            pass
        _enable_disable_extensions.assert_not_called()


def test_grant_internal_access_group_memberships(harness):
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        harness.charm.postgresql.grant_internal_access_group_memberships()

        internal_group = Identifier(ACCESS_GROUP_INTERNAL)

        execute.assert_has_calls([
            *(
                call(SQL("GRANT {} TO {};").format(internal_group, Identifier(user)))
                for user in SYSTEM_USERS
            ),
        ])


def test_grant_relation_access_group_memberships(harness):
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        harness.charm.postgresql.grant_relation_access_group_memberships()

        execute.assert_has_calls([
            call(
                "SELECT usename FROM pg_catalog.pg_user WHERE usename LIKE 'relation_id_%' OR usename LIKE 'relation-%' OR usename LIKE 'pgbouncer_auth_relation_%' OR usename LIKE '%_user_%_%' OR usename LIKE 'logical_replication_relation_%';"
            )
        ])


def test_get_last_archived_wal(harness):
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        # Test a successful call.
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            "000000010000000100000001",
        )
        assert harness.charm.postgresql.get_last_archived_wal() == "000000010000000100000001"
        execute.assert_called_once_with("SELECT last_archived_wal FROM pg_stat_archiver;")

        # Test a failed call.
        execute.reset_mock()
        execute.side_effect = psycopg2.Error
        try:
            harness.charm.postgresql.get_last_archived_wal()
            assert False
        except PostgreSQLGetLastArchivedWALError:
            pass
        execute.assert_called_once_with("SELECT last_archived_wal FROM pg_stat_archiver;")


def test_build_postgresql_group_map(harness):
    assert harness.charm.postgresql.build_postgresql_group_map(None) == []

    for group in ACCESS_GROUPS:
        assert harness.charm.postgresql.build_postgresql_group_map(f"ldap_group={group}") == []

    mapping_1 = "ldap_group_1=psql_group_1"
    mapping_2 = "ldap_group_2=psql_group_2"

    assert harness.charm.postgresql.build_postgresql_group_map(f"{mapping_1},{mapping_2}") == [
        ("ldap_group_1", "psql_group_1"),
        ("ldap_group_2", "psql_group_2"),
    ]
    try:
        harness.charm.postgresql.build_postgresql_group_map(f"{mapping_1} {mapping_2}")
        assert False
    except ValueError:
        assert True


def test_build_postgresql_parameters(harness):
    # Test when not limit is imposed to the available memory.
    config_options = {
        "durability_test_config_option_1": True,
        "instance_test_config_option_2": False,
        "logging_test_config_option_3": "on",
        "memory_test_config_option_4": 1024,
        "optimizer_test_config_option_5": "scheduled",
        "optimizer_pg_stat_statements_max": 2,
        "optimizer-pg-stat-statements-track-utility": True,
        "other_test_config_option_6": "test-value",
        "profile": "production",
        "request_date_style": "ISO, DMY",
        "request-time-zone": "UTC",
        "request_test_config_option_7": "off",
        "response_test_config_option_8": "partial",
        "vacuum_test_config_option_9": 10.5,
        "durability-maximum-lag-on-failover": 1024,
    }
    assert harness.charm.postgresql.build_postgresql_parameters(config_options, 1000000000) == {
        "test_config_option_1": True,
        "test_config_option_2": False,
        "test_config_option_3": "on",
        "test_config_option_4": 1024,
        "test_config_option_5": "scheduled",
        "test_config_option_7": "off",
        "DateStyle": "ISO, DMY",
        "TimeZone": "UTC",
        "test_config_option_8": "partial",
        "test_config_option_9": 10.5,
        "pg_stat_statements.max": 2,
        "pg_stat_statements.track_utility": True,
        "shared_buffers": f"{250 * 128}",
        "effective_cache_size": f"{750 * 128}",
    }

    # Test with a limited imposed to the available memory.
    parameters = harness.charm.postgresql.build_postgresql_parameters(
        config_options, 1000000000, 600000000
    )
    assert parameters["shared_buffers"] == f"{150 * 128}"
    assert parameters["effective_cache_size"] == f"{450 * 128}"

    # Test when the requested shared buffers are greater than 40% of the available memory.
    config_options["memory_shared_buffers"] = 50001
    try:
        harness.charm.postgresql.build_postgresql_parameters(config_options, 1000000000)
        assert False
    except AssertionError as e:
        raise e
    except Exception:
        pass

    # Test when the requested shared buffers are lower than 40% of the available memory
    # (also check that it's used when calculating the effective cache size value).
    config_options["memory_shared_buffers"] = 50000
    parameters = harness.charm.postgresql.build_postgresql_parameters(config_options, 1000000000)
    assert parameters["shared_buffers"] == 50000
    assert parameters["effective_cache_size"] == f"{600 * 128}"

    # Test when the profile is set to "testing".
    config_options["profile"] = "testing"
    parameters = harness.charm.postgresql.build_postgresql_parameters(config_options, 1000000000)
    assert parameters["shared_buffers"] == 50000
    assert "effective_cache_size" not in parameters

    # Test when there is no shared_buffers value set in the config option.
    del config_options["memory_shared_buffers"]
    parameters = harness.charm.postgresql.build_postgresql_parameters(config_options, 1000000000)
    assert "shared_buffers" not in parameters
    assert "effective_cache_size" not in parameters


def test_configure_pgaudit(harness):
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        # Test when pgAudit is enabled.
        execute = (
            _connect_to_database.return_value.cursor.return_value.__enter__.return_value.execute
        )
        harness.charm.postgresql._configure_pgaudit(True)
        execute.assert_has_calls([
            call("ALTER SYSTEM SET pgaudit.log = 'ROLE,DDL,MISC,MISC_SET';"),
            call("ALTER SYSTEM SET pgaudit.log_client TO off;"),
            call("ALTER SYSTEM SET pgaudit.log_parameter TO off;"),
            call("SELECT pg_reload_conf();"),
        ])

        # Test when pgAudit is disabled.
        execute.reset_mock()
        harness.charm.postgresql._configure_pgaudit(False)
        execute.assert_has_calls([
            call("ALTER SYSTEM RESET pgaudit.log;"),
            call("ALTER SYSTEM RESET pgaudit.log_client;"),
            call("ALTER SYSTEM RESET pgaudit.log_parameter;"),
            call("SELECT pg_reload_conf();"),
        ])


def test_validate_group_map(harness):
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = None

        query = SQL("SELECT TRUE FROM pg_roles WHERE rolname={};")

        assert harness.charm.postgresql.validate_group_map(None) is True

        assert harness.charm.postgresql.validate_group_map("") is False
        assert harness.charm.postgresql.validate_group_map("ldap_group=") is False
        execute.assert_has_calls([
            call(query.format(Literal(""))),
        ])

        assert harness.charm.postgresql.validate_group_map("ldap_group=missing_group") is False
        execute.assert_has_calls([
            call(query.format(Literal("missing_group"))),
        ])

        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            True,
        )
        assert harness.charm.postgresql.validate_group_map("ldap_group=ldap_test_group") is True
        assert harness.charm.postgresql.validate_group_map("ldap_group=ldap_test_group,") is False
        assert harness.charm.postgresql.validate_group_map("ldap_group ldap_test_group") is False


def test_set_up_database_with_temp_tablespace_and_missing_owner_role(harness, substrate):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.create_user") as _create_user,
        patch("single_kernel_postgresql.utils.postgresql.change_owner") as _change_owner,
        patch("single_kernel_postgresql.utils.postgresql.os.chmod") as _chmod,
        patch("single_kernel_postgresql.utils.postgresql.os.stat") as _stat,
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
    ):
        # Simulate a temp location owned by wrong user/permissions to trigger fixup (33188 means 0o644)
        stat_result = type("stat_result", (), {"st_uid": 0, "st_gid": 0, "st_mode": 33188})
        _stat.return_value = stat_result
        _getpwuid.return_value.pw_name = "root"
        _getpwuid.return_value.pw_uid = 0
        _getpwuid.return_value.pw_gid = 0

        # First connection (non-context) for temp tablespace
        execute_direct = _connect_to_database.return_value.cursor.return_value.execute
        fetchone_direct = _connect_to_database.return_value.cursor.return_value.fetchone
        fetchone_direct.return_value = None

        # Second and third connections are context-managed
        execute_cm = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        fetchone_cm = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone
        fetchone_cm.return_value = None  # owner role missing

        harness.charm.postgresql.set_up_database(temp_location="/var/lib/postgresql/tmp")

        # Ensure permission fixes applied
        if substrate == "k8s":
            _change_owner.assert_not_called()
            _chmod.assert_not_called()
            print(execute_direct.call_args_list)
        else:
            _change_owner.assert_called_once_with("/var/lib/postgresql/tmp")
            _chmod.assert_called_once_with("/var/lib/postgresql/tmp", 0o700)

        # Validate temp tablespace operations: check existence and create/grant when missing
        if substrate == "k8s":
            execute_direct.assert_has_calls([
                call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';"),
                call("CREATE TABLESPACE temp LOCATION '/var/lib/postgresql/tmp';"),
                call("GRANT CREATE ON TABLESPACE temp TO public;"),
            ])
        else:
            execute_direct.assert_has_calls([
                call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';"),
                call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';"),
                call("CREATE TABLESPACE temp LOCATION '/var/lib/postgresql/tmp';"),
                call("GRANT CREATE ON TABLESPACE temp TO public;"),
            ])

        # create_user called for missing owner role
        _create_user.assert_called_once_with(
            ROLE_DATABASES_OWNER, can_create_database=True, extra_user_roles=["charmed_dml"]
        )

        # Final revokes and grants
        system_users = harness.charm.postgresql.system_users
        expected = [
            call("REVOKE ALL PRIVILEGES ON DATABASE postgres FROM PUBLIC;"),
            call("REVOKE CREATE ON SCHEMA public FROM PUBLIC;"),
            *[
                call(SQL("GRANT ALL PRIVILEGES ON DATABASE postgres TO {};").format(Identifier(u)))
                for u in system_users
            ],
        ]
        execute_cm.assert_has_calls(expected, any_order=False)


def test_set_up_database_owner_mismatch_triggers_rename_and_fix(harness, substrate):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.change_owner") as _change_owner,
        patch("single_kernel_postgresql.utils.postgresql.os.chmod") as _chmod,
        patch("single_kernel_postgresql.utils.postgresql.os.stat") as _stat,
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
        patch("single_kernel_postgresql.utils.postgresql.datetime") as _dt,
        patch("single_kernel_postgresql.utils.postgresql.is_tmpfs") as _is_tmpfs,
    ):
        # Simulate tmpfs storage for this test
        _is_tmpfs.return_value = True

        # Owner differs, permissions are correct (16832 means 0o700)
        # Simulate directory owned by uid 1000 while expected owner has uid 0 to force mismatch
        stat_result = type("stat_result", (), {"st_uid": 1000, "st_gid": 1000, "st_mode": 16832})
        _stat.return_value = stat_result
        # The expected owner (SNAP_USER) resolves to uid 0/gid 0 for the test
        _getpwuid.return_value.pw_name = "root"
        _getpwuid.return_value.pw_uid = 0
        _getpwuid.return_value.pw_gid = 0

        # Mock datetime.now(timezone.utc) to a fixed timestamp
        _dt.now.return_value = datetime(2025, 1, 1, 1, 2, 3, tzinfo=UTC)
        _dt.timezone = timezone  # ensure timezone.utc is available in the patch target

        execute_direct = _connect_to_database.return_value.cursor.return_value.execute
        fetchone_direct = _connect_to_database.return_value.cursor.return_value.fetchone
        fetchone_direct.side_effect = [True, None]

        harness.charm.postgresql.set_up_database(temp_location="/var/lib/postgresql/tmp")
        if substrate == "k8s":
            _change_owner.assert_not_called()
            _chmod.assert_not_called()
            execute_direct.assert_any_call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
        else:
            _change_owner.assert_called_once_with("/var/lib/postgresql/tmp")
            _chmod.assert_called_once_with(
                "/var/lib/postgresql/tmp", POSTGRESQL_STORAGE_PERMISSIONS
            )
            execute_direct.assert_any_call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
            execute_direct.assert_any_call("ALTER TABLESPACE temp RENAME TO temp_20250101010203;")


def test_set_up_database_permissions_mismatch_triggers_rename_and_fix(harness, substrate):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.change_owner") as _change_owner,
        patch("single_kernel_postgresql.utils.postgresql.os.chmod") as _chmod,
        patch("single_kernel_postgresql.utils.postgresql.os.stat") as _stat,
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
        patch("single_kernel_postgresql.utils.postgresql.datetime") as _dt,
        patch("single_kernel_postgresql.utils.postgresql.is_tmpfs") as _is_tmpfs,
    ):
        # Simulate tmpfs storage for this test
        _is_tmpfs.return_value = True

        # Owner matches SNAP_USER, permissions differ (33188 means 0o644)
        stat_result = type("stat_result", (), {"st_uid": 0, "st_gid": 0, "st_mode": 33188})
        _stat.return_value = stat_result
        _getpwuid.return_value.pw_name = SNAP_USER
        _getpwuid.return_value.pw_uid = 0
        _getpwuid.return_value.pw_gid = 0

        # Mock datetime.now(timezone.utc) to a fixed timestamp
        _dt.now.return_value = datetime(2025, 1, 1, 1, 2, 3, tzinfo=UTC)
        _dt.timezone = timezone

        execute_direct = _connect_to_database.return_value.cursor.return_value.execute
        fetchone_direct = _connect_to_database.return_value.cursor.return_value.fetchone
        fetchone_direct.side_effect = [True, None]

        harness.charm.postgresql.set_up_database(temp_location="/var/lib/postgresql/tmp")
        if substrate == "k8s":
            _change_owner.assert_not_called()
            _chmod.assert_not_called()
            execute_direct.assert_any_call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
        else:
            _change_owner.assert_called_once_with("/var/lib/postgresql/tmp")
            _chmod.assert_called_once_with(
                "/var/lib/postgresql/tmp", POSTGRESQL_STORAGE_PERMISSIONS
            )
            execute_direct.assert_any_call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
            execute_direct.assert_any_call("ALTER TABLESPACE temp RENAME TO temp_20250101010203;")


def test_set_up_database_persistent_storage_no_rename(harness, substrate):
    """Test that persistent storage permissions fix doesn't rename tablespace."""
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.change_owner") as _change_owner,
        patch("single_kernel_postgresql.utils.postgresql.os.chmod") as _chmod,
        patch("single_kernel_postgresql.utils.postgresql.os.stat") as _stat,
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
        patch("single_kernel_postgresql.utils.postgresql.is_tmpfs") as _is_tmpfs,
    ):
        # Simulate persistent storage (not tmpfs)
        _is_tmpfs.return_value = False

        # Permissions need fixing (wrong owner)
        stat_result = type("stat_result", (), {"st_uid": 1000, "st_gid": 1000, "st_mode": 16832})
        _stat.return_value = stat_result
        _getpwuid.return_value.pw_name = "root"

        execute = _connect_to_database.return_value.cursor.return_value.execute
        fetchone = _connect_to_database.return_value.cursor.return_value.fetchone
        # First check: tablespace exists, second check: still exists (not renamed)
        fetchone.side_effect = [True, True]

        harness.charm.postgresql.set_up_database(temp_location="/var/lib/postgresql/tmp")

        # Permissions should be fixed
        if substrate == "k8s":
            _change_owner.assert_not_called()
            _chmod.assert_not_called()
        else:
            _change_owner.assert_called_once_with("/var/lib/postgresql/tmp")
            _chmod.assert_called_once_with(
                "/var/lib/postgresql/tmp", POSTGRESQL_STORAGE_PERMISSIONS
            )

        # Tablespace should NOT be renamed
        for call in execute.call_args_list:
            call_str = str(call)
            assert "ALTER TABLESPACE temp RENAME" not in call_str, f"Found rename in: {call_str}"

        # Should NOT try to create new tablespace (because it still exists)
        create_calls = [
            call for call in execute.call_args_list if "CREATE TABLESPACE temp" in str(call)
        ]
        assert len(create_calls) == 0, f"Unexpected CREATE TABLESPACE calls: {create_calls}"


def test_set_up_database_no_temp_and_existing_owner_role(harness):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.create_user") as _create_user,
    ):
        # owner role exists
        fetchone = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone
        fetchone.return_value = True

        harness.charm.postgresql.set_up_database()

        _create_user.assert_not_called()

        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute
        system_users = harness.charm.postgresql.system_users
        execute.assert_has_calls([
            call("REVOKE ALL PRIVILEGES ON DATABASE postgres FROM PUBLIC;"),
            call("REVOKE CREATE ON SCHEMA public FROM PUBLIC;"),
            *[
                call(SQL("GRANT ALL PRIVILEGES ON DATABASE postgres TO {};").format(Identifier(u)))
                for u in system_users
            ],
        ])


def test_set_up_database_raises_wrapped_error(harness):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.change_owner"),
        patch("single_kernel_postgresql.utils.postgresql.os.chmod"),
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
    ):
        # Provide a dummy passwd entry so has_correct_ownership_and_permissions does not raise
        _getpwuid.return_value.pw_name = SNAP_USER
        _getpwuid.return_value.pw_uid = 0
        _getpwuid.return_value.pw_gid = 0

        execute_direct = _connect_to_database.return_value.cursor.return_value.execute
        execute_direct.side_effect = psycopg2.Error
        with pytest.raises(PostgreSQLDatabasesSetupError):
            harness.charm.postgresql.set_up_database(temp_location="/tmp")


def test_connect_to_database():
    # Error on no host
    pg = PostgreSQL(Substrates.VM, None, None, "operator", None, "postgres", None)
    with pytest.raises(PostgreSQLUndefinedHostError):
        pg._connect_to_database()

    # Error on no password
    pg = PostgreSQL(Substrates.VM, "primary", "current", "operator", None, "postgres", None)
    with pytest.raises(PostgreSQLUndefinedPasswordError):
        pg._connect_to_database()

    # Returns connection
    pg = PostgreSQL(Substrates.VM, "primary", "current", "operator", "password", "postgres", None)
    with patch(
        "single_kernel_postgresql.utils.postgresql.psycopg2.connect",
        return_value=sentinel.connection,
    ) as _connect:
        assert pg._connect_to_database() == sentinel.connection
        _connect.assert_called_once_with(
            "dbname='postgres' user='operator' host='primary'password='password' connect_timeout=1"
        )


def test_is_user_in_hba():
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database",
    ) as _connect_to_database:
        pg = PostgreSQL(
            Substrates.VM, "primary", "current", "operator", "password", "postgres", None
        )
        _cursor = _connect_to_database().__enter__().cursor().__enter__()

        # No result
        _cursor.fetchone.return_value = None
        assert pg.is_user_in_hba("test-user") is False
        _cursor.execute.assert_called_once_with(
            Composed([
                SQL("SELECT COUNT(*) FROM pg_hba_file_rules WHERE "),
                Literal("test-user"),
                SQL(" = ANY(user_name);"),
            ])
        )

        # Exception
        _cursor.fetchone.side_effect = psycopg2.Error
        assert pg.is_user_in_hba("test-user") is False

        # Result
        _cursor.fetchone.side_effect = None
        _cursor.fetchone.return_value = (1,)
        assert pg.is_user_in_hba("test-user") is True


def test_drop_hba_triggers():
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database",
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.logger") as _logger,
    ):
        pg = PostgreSQL(
            Substrates.VM, "primary", "current", "operator", "password", "postgres", None
        )
        _cursor = _connect_to_database().__enter__().cursor().__enter__()
        _cursor.fetchall.return_value = (("db1",), ("db2",))

        pg.drop_hba_triggers()

        assert _cursor.execute.call_count == 5
        _cursor.execute.assert_any_call(
            SQL(
                "SELECT datname FROM pg_database WHERE datname <> 'template0' AND datname <>'postgres';"
            )
        )
        _cursor.execute.assert_any_call(
            SQL("DROP EVENT TRIGGER IF EXISTS update_pg_hba_on_create_schema;")
        )
        _cursor.execute.assert_any_call(
            SQL("DROP EVENT TRIGGER IF EXISTS update_pg_hba_on_drop_schema;")
        )
        _cursor.execute.reset_mock()

        # Exception on select
        _cursor.execute.side_effect = psycopg2.Error

        pg.drop_hba_triggers()

        _cursor.execute.assert_called_once_with(
            SQL(
                "SELECT datname FROM pg_database WHERE datname <> 'template0' AND datname <>'postgres';"
            )
        )
        _logger.warning.assert_called_once_with(
            "Failed to get databases when removing hba trigger: "
        )
        _logger.warning.reset_mock()

        # Exception on drop
        _cursor.execute.side_effect = [None, psycopg2.Error, None, None]

        pg.drop_hba_triggers()

        _logger.warning.assert_called_once_with("Failed to remove hba trigger for db1: ")


def test_create_user():
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database",
        ) as _connect_to_database,
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._process_extra_user_roles",
        ) as _process_extra_user_roles,
    ):
        pg = PostgreSQL(
            Substrates.VM, "primary", "current", "operator", "password", "postgres", None
        )
        _cursor = _connect_to_database().__enter__().cursor().__enter__()
        _process_extra_user_roles.return_value = (["role1", "role2"], ["priv1", "priv2"])

        # Create user
        _cursor.fetchone.return_value = None

        pg.create_user("username", "password")

        assert _cursor.execute.call_count == 8
        _cursor.execute.assert_any_call(
            Composed([
                SQL("SELECT TRUE FROM pg_roles WHERE rolname="),
                Literal("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.assert_any_call(SQL("RESET ROLE;"))
        _cursor.execute.assert_any_call(SQL("BEGIN;"))
        _cursor.execute.assert_any_call(SQL("SET LOCAL log_statement = 'none';"))
        _cursor.execute.assert_any_call(
            Composed([
                SQL("CREATE ROLE "),
                Identifier("username"),
                SQL(" WITH LOGIN ENCRYPTED PASSWORD 'password' priv1 priv2;"),
            ])
        )
        _cursor.execute.assert_any_call(SQL("COMMIT;"))
        _cursor.execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("role1"),
                SQL(" TO "),
                Identifier("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("role2"),
                SQL(" TO "),
                Identifier("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.reset_mock()
        _process_extra_user_roles.reset_mock()

        # Alter user
        _cursor.fetchone.return_value = (1,)

        pg.create_user("username", "password", True, True, ["role3"], "db1", True)

        _process_extra_user_roles.assert_called_once_with("username", ["role3"])
        assert _cursor.execute.call_count == 10
        _cursor.execute.assert_any_call(
            Composed([
                SQL("SELECT TRUE FROM pg_roles WHERE rolname="),
                Literal("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.assert_any_call(SQL("RESET ROLE;"))
        _cursor.execute.assert_any_call(SQL("BEGIN;"))
        _cursor.execute.assert_any_call(SQL("SET LOCAL log_statement = 'none';"))
        _cursor.execute.assert_any_call(
            Composed([
                SQL("ALTER ROLE "),
                Identifier("username"),
                SQL(" WITH LOGIN SUPERUSER REPLICATION ENCRYPTED PASSWORD 'password';"),
            ])
        )
        _cursor.execute.assert_any_call(SQL("COMMIT;"))
        _cursor.execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("charmed_db1_admin"),
                SQL(" TO "),
                Identifier("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("charmed_db1_dml"),
                SQL(" TO "),
                Identifier("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("role1"),
                SQL(" TO "),
                Identifier("username"),
                SQL(";"),
            ])
        )
        _cursor.execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("role2"),
                SQL(" TO "),
                Identifier("username"),
                SQL(";"),
            ])
        )

        # Exception
        _cursor.execute.side_effect = psycopg2.Error

        with pytest.raises(PostgreSQLCreateUserError):
            pg.create_user("username", "password")


def test_set_up_database_owner_and_permissions_match_no_rename_or_fix(harness):
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.change_owner") as _change_owner,
        patch("single_kernel_postgresql.utils.postgresql.os.chmod") as _chmod,
        patch("single_kernel_postgresql.utils.postgresql.os.stat") as _stat,
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
    ):
        # Owner matches SNAP_USER and permissions are correct (16832 means 0o700)
        stat_result = type("stat_result", (), {"st_uid": 0, "st_gid": 0, "st_mode": 16832})
        _stat.return_value = stat_result
        _getpwuid.return_value.pw_name = SNAP_USER
        _getpwuid.return_value.pw_uid = 0
        _getpwuid.return_value.pw_gid = 0

        execute_direct = _connect_to_database.return_value.cursor.return_value.execute
        fetchone_direct = _connect_to_database.return_value.cursor.return_value.fetchone
        # No mismatch, so the existence check returns True and no creation/rename occurs
        fetchone_direct.return_value = True

        harness.charm.postgresql.set_up_database(temp_location="/var/lib/postgresql/tmp")

        # No permission/owner fix should be performed
        _change_owner.assert_not_called()
        _chmod.assert_not_called()

        # It should check for temp tablespace existence
        execute_direct.assert_any_call("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")

        # Ensure that no rename was attempted
        for c in execute_direct.call_args_list:
            if c.args:
                assert "ALTER TABLESPACE temp RENAME TO" not in c.args[0]


def test_set_up_database_k8s_skips_change_owner_and_chmod(harness, substrate):
    """When running on the K8S substrate, filesystem ownership/permission fixes should be skipped.

    Even if the on-disk owner/permissions appear incorrect, change_owner and os.chmod must
    not be called for Substrates.K8S.
    """
    # Skip test if not running on k8s
    if substrate != "k8s":
        pytest.skip("Test only applicable for K8S substrate")

    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch("single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_login_hook_function"),
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL.set_up_predefined_catalog_roles_function"
        ),
        patch("single_kernel_postgresql.utils.postgresql.change_owner") as _change_owner,
        patch("single_kernel_postgresql.utils.postgresql.os.chmod") as _chmod,
        patch("single_kernel_postgresql.utils.postgresql.os.stat") as _stat,
        patch("single_kernel_postgresql.utils.postgresql.pwd.getpwuid") as _getpwuid,
    ):
        # Simulate a temp location owned by wrong user/permissions which would normally
        # trigger a fixup when running on VM substrate.
        stat_result = type("stat_result", (), {"st_uid": 0, "st_gid": 0, "st_mode": 33188})
        _stat.return_value = stat_result
        _getpwuid.return_value.pw_name = "root"
        _getpwuid.return_value.pw_uid = 0
        _getpwuid.return_value.pw_gid = 0

        # Force the charm's PostgreSQL instance to think it's running on K8S.
        harness.charm.postgresql.substrate = Substrates.K8S

        harness.charm.postgresql.set_up_database(temp_location="/var/lib/postgresql/tmp")

        # On K8S substrate we must not attempt to change ownership or chmod the path.
        _change_owner.assert_not_called()
        _chmod.assert_not_called()


def test_list_databases():
    with patch(
        "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database",
    ) as _connect_to_database:
        pg = PostgreSQL(
            Substrates.VM, "primary", "current", "operator", "password", "postgres", None
        )
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute

        # No prefix
        pg.list_databases()
        execute.assert_called_once_with(
            Composed([
                SQL(
                    "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <>'postgres'"
                ),
                SQL(""),
                SQL(";"),
            ])
        )
        execute.reset_mock()

        # With prefix
        pg.list_databases(prefix="test")
        execute.assert_called_once_with(
            Composed([
                SQL(
                    "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <>'postgres'"
                ),
                Composed([SQL(" AND datname LIKE "), Literal("test%")]),
                SQL(";"),
            ])
        )
        execute.reset_mock()

        # Exception
        execute.side_effect = psycopg2.Error
        with pytest.raises(PostgreSQLListDatabasesError):
            pg.list_databases()
            assert False


def test_add_user_to_databases():
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._process_extra_user_roles",
            return_value=([], []),
        ),
    ):
        pg = PostgreSQL(
            Substrates.VM, "primary", "current", "operator", "password", "postgres", None
        )
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute

        pg.add_user_to_databases("test-user", ["db1", "db2"])
        assert execute.call_count == 8
        execute.assert_any_call(SQL("RESET ROLE;"))
        execute.assert_any_call(SQL("BEGIN;"))
        execute.assert_any_call(SQL("SET LOCAL log_statement = 'none';"))
        execute.assert_any_call(SQL("COMMIT;"))
        execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("charmed_db1_admin"),
                SQL(" TO "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("charmed_db1_dml"),
                SQL(" TO "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("charmed_db2_admin"),
                SQL(" TO "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("GRANT "),
                Identifier("charmed_db2_dml"),
                SQL(" TO "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )

        # Exception
        execute.side_effect = psycopg2.Error
        with pytest.raises(PostgreSQLUpdateUserError):
            pg.add_user_to_databases("test-user", ["db1", "db2"])
            assert False


def test_remove_user_from_databases():
    with (
        patch(
            "single_kernel_postgresql.utils.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
    ):
        pg = PostgreSQL(
            Substrates.VM, "primary", "current", "operator", "password", "postgres", None
        )
        execute = _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute

        pg.remove_user_from_databases("test-user", ["db1", "db2"])
        assert execute.call_count == 8
        execute.assert_any_call(
            Composed([
                SQL("REVOKE CONNECT ON DATABASE "),
                Identifier("db1"),
                SQL(" FROM "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("REVOKE CONNECT ON DATABASE "),
                Identifier("db2"),
                SQL(" FROM "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("REVOKE "),
                Identifier("charmed_db1_admin"),
                SQL(" FROM "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("REVOKE "),
                Identifier("charmed_db1_dml"),
                SQL(" FROM "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("REVOKE "),
                Identifier("charmed_db2_admin"),
                SQL(" FROM "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )
        execute.assert_any_call(
            Composed([
                SQL("REVOKE "),
                Identifier("charmed_db2_dml"),
                SQL(" FROM "),
                Identifier("test-user"),
                SQL(";"),
            ])
        )

        # Exception
        execute.side_effect = psycopg2.Error
        with pytest.raises(PostgreSQLUpdateUserError):
            pg.remove_user_from_databases("test-user", ["db1", "db2"])
            assert False
