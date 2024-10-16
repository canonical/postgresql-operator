# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import call, patch

import psycopg2
import pytest
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLGetLastArchivedWALError,
)
from ops.testing import Harness
from psycopg2.sql import SQL, Composed, Identifier

from charm import PostgresqlOperatorCharm
from constants import PEER


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    peer_rel_id = harness.add_relation(PEER, "postgresql")
    harness.add_relation_unit(peer_rel_id, "postgresql/0")
    harness.begin()
    yield harness
    harness.cleanup()


def test_create_database(harness):
    with (
        patch(
            "charms.postgresql_k8s.v0.postgresql.PostgreSQL.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch(
            "charms.postgresql_k8s.v0.postgresql.PostgreSQL._generate_database_privileges_statements"
        ) as _generate_database_privileges_statements,
        patch(
            "charms.postgresql_k8s.v0.postgresql.PostgreSQL._connect_to_database"
        ) as _connect_to_database,
    ):
        # Test a successful database creation.
        database = "test_database"
        user = "test_user"
        plugins = ["test_plugin_1", "test_plugin_2"]
        with harness.hooks_disabled():
            rel_id = harness.add_relation("database", "application")
            harness.add_relation_unit(rel_id, "application/0")
            harness.update_relation_data(rel_id, "application", {"database": database})
        database_relation = harness.model.get_relation("database")
        client_relations = [database_relation]
        schemas = [("test_schema_1",), ("test_schema_2",)]
        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = schemas
        harness.charm.postgresql.create_database(database, user, plugins, client_relations)
        execute = _connect_to_database.return_value.cursor.return_value.execute
        execute.assert_has_calls([
            call(
                Composed([
                    SQL("REVOKE ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" FROM PUBLIC;"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier(user),
                    SQL(";"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier("admin"),
                    SQL(";"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier("backup"),
                    SQL(";"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier("replication"),
                    SQL(";"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier("rewind"),
                    SQL(";"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier("operator"),
                    SQL(";"),
                ])
            ),
            call(
                Composed([
                    SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                    Identifier(database),
                    SQL(" TO "),
                    Identifier("monitoring"),
                    SQL(";"),
                ])
            ),
        ])
        _generate_database_privileges_statements.assert_called_once_with(
            1, [schemas[0][0], schemas[1][0]], user
        )
        _enable_disable_extensions.assert_called_once_with(
            {plugins[0]: True, plugins[1]: True}, database
        )

        # Test when two relations request the same database.
        _connect_to_database.reset_mock()
        _generate_database_privileges_statements.reset_mock()
        with harness.hooks_disabled():
            other_rel_id = harness.add_relation("database", "other-application")
            harness.add_relation_unit(other_rel_id, "other-application/0")
            harness.update_relation_data(other_rel_id, "other-application", {"database": database})
        other_database_relation = harness.model.get_relation("database", other_rel_id)
        client_relations = [database_relation, other_database_relation]
        harness.charm.postgresql.create_database(database, user, plugins, client_relations)
        _generate_database_privileges_statements.assert_called_once_with(
            2, [schemas[0][0], schemas[1][0]], user
        )

        # Test a failed database creation.
        _enable_disable_extensions.reset_mock()
        execute.side_effect = psycopg2.Error
        try:
            harness.charm.postgresql.create_database(database, user, plugins, client_relations)
            assert False
        except PostgreSQLCreateDatabaseError:
            pass
        _enable_disable_extensions.assert_not_called()


def test_generate_database_privileges_statements(harness):
    # Test with only one established relation.
    assert harness.charm.postgresql._generate_database_privileges_statements(
        1, ["test_schema_1", "test_schema_2"], "test_user"
    ) == [
        Composed([
            SQL(
                "DO $$\nDECLARE r RECORD;\nBEGIN\n  FOR r IN (SELECT statement FROM (SELECT 1 AS index,'ALTER TABLE '|| schemaname || '.\"' || tablename ||'\" OWNER TO "
            ),
            Identifier("test_user"),
            SQL(
                ";' AS statement\nFROM pg_tables WHERE NOT schemaname IN ('pg_catalog', 'information_schema')\nUNION SELECT 2 AS index,'ALTER SEQUENCE '|| sequence_schema || '.\"' || sequence_name ||'\" OWNER TO "
            ),
            Identifier("test_user"),
            SQL(
                ";' AS statement\nFROM information_schema.sequences WHERE NOT sequence_schema IN ('pg_catalog', 'information_schema')\nUNION SELECT 3 AS index,'ALTER FUNCTION '|| nsp.nspname || '.\"' || p.proname ||'\"('||pg_get_function_identity_arguments(p.oid)||') OWNER TO "
            ),
            Identifier("test_user"),
            SQL(
                ";' AS statement\nFROM pg_proc p JOIN pg_namespace nsp ON p.pronamespace = nsp.oid WHERE NOT nsp.nspname IN ('pg_catalog', 'information_schema')\nUNION SELECT 4 AS index,'ALTER VIEW '|| schemaname || '.\"' || viewname ||'\" OWNER TO "
            ),
            Identifier("test_user"),
            SQL(
                ";' AS statement\nFROM pg_catalog.pg_views WHERE NOT schemaname IN ('pg_catalog', 'information_schema')) AS statements ORDER BY index) LOOP\n      EXECUTE format(r.statement);\n  END LOOP;\nEND; $$;"
            ),
        ]),
        "UPDATE pg_catalog.pg_largeobject_metadata\nSET lomowner = (SELECT oid FROM pg_roles WHERE rolname = 'test_user')\nWHERE lomowner = (SELECT oid FROM pg_roles WHERE rolname = 'operator');",
        Composed([
            SQL("ALTER SCHEMA "),
            Identifier("test_schema_1"),
            SQL(" OWNER TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("ALTER SCHEMA "),
            Identifier("test_schema_2"),
            SQL(" OWNER TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
    ]
    # Test with multiple established relations.
    assert harness.charm.postgresql._generate_database_privileges_statements(
        2, ["test_schema_1", "test_schema_2"], "test_user"
    ) == [
        Composed([
            SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA "),
            Identifier("test_schema_1"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "),
            Identifier("test_schema_1"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "),
            Identifier("test_schema_1"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT USAGE ON SCHEMA "),
            Identifier("test_schema_1"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT CREATE ON SCHEMA "),
            Identifier("test_schema_1"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA "),
            Identifier("test_schema_2"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "),
            Identifier("test_schema_2"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "),
            Identifier("test_schema_2"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT USAGE ON SCHEMA "),
            Identifier("test_schema_2"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
        Composed([
            SQL("GRANT CREATE ON SCHEMA "),
            Identifier("test_schema_2"),
            SQL(" TO "),
            Identifier("test_user"),
            SQL(";"),
        ]),
    ]


def test_get_last_archived_wal(harness):
    with patch(
        "charms.postgresql_k8s.v0.postgresql.PostgreSQL._connect_to_database"
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


def test_build_postgresql_parameters(harness):
    # Test when not limit is imposed to the available memory.
    config_options = {
        "durability_test_config_option_1": True,
        "instance_test_config_option_2": False,
        "logging_test_config_option_3": "on",
        "memory_test_config_option_4": 1024,
        "optimizer_test_config_option_5": "scheduled",
        "other_test_config_option_6": "test-value",
        "profile": "production",
        "request_date_style": "ISO, DMY",
        "request_time_zone": "UTC",
        "request_test_config_option_7": "off",
        "response_test_config_option_8": "partial",
        "vacuum_test_config_option_9": 10.5,
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
        "charms.postgresql_k8s.v0.postgresql.PostgreSQL._connect_to_database"
    ) as _connect_to_database:
        # Test when pgAudit is enabled.
        execute = (
            _connect_to_database.return_value.cursor.return_value.__enter__.return_value.execute
        )
        harness.charm.postgresql._configure_pgaudit(True)
        execute.assert_has_calls([
            call("ALTER SYSTEM SET pgaudit.log = 'ROLE,DDL,MISC,MISC_SET';"),
            call("ALTER SYSTEM SET pgaudit.log_client TO off;"),
            call("ALTER SYSTEM SET pgaudit.log_parameter TO off"),
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
