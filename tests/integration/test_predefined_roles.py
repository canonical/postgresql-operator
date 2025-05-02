#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
)
from .new_relations.helpers import build_connection_string

logger = logging.getLogger(__name__)

TIMEOUT = 15 * 60
DATA_INTEGRATOR_APP_NAME = "data-integrator"

# NOTE: We are unable to test set_user_u('operator') as dba user because psycopg2
# runs every query in a transaction and running set_user() is not supported in transactions.


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_deploy(ops_test: OpsTest, charm: str):
    """Deploy the postgresql charm."""
    async with ops_test.fast_forward("10s"):
        await asyncio.gather(
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=2,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            ops_test.model.deploy(
                DATA_INTEGRATOR_APP_NAME,
                base=CHARM_BASE,
            ),
        )

        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT
        )
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME], status="blocked", timeout=(5 * 60)
        )


@pytest.mark.abort_on_fail
async def test_charmed_read_role(ops_test: OpsTest):
    """Test the charmed_read predefined role."""
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
        "database-name": "charmed_read_database",
        "extra-user-roles": "charmed_read",
    })
    await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
    )

    connection_string = await build_connection_string(
        ops_test,
        DATA_INTEGRATOR_APP_NAME,
        "postgresql",
        database="charmed_read_database",
    )

    with psycopg2.connect(connection_string) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test_table (id SERIAL PRIMARY KEY, data TEXT);")
            cursor.execute("INSERT INTO test_table (data) VALUES ('test_data'), ('test_data_2');")

            # reset role from charmed_read_database_owner to relation user first
            cursor.execute("RESET ROLE;")

            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name NOT LIKE 'pg_%' AND table_name NOT LIKE 'sql_%' AND table_type <> 'VIEW';"
            )
            tables = [row[0] for row in cursor.fetchall()]
            assert tables == ["test_table"], "Unexpected tables in the database"

            cursor.execute("SELECT data FROM test_table;")
            data = sorted([row[0] for row in cursor.fetchall()])
            assert data == sorted(["test_data", "test_data_2"]), (
                "Unexpected data in charmed_read_database with charmed_read role"
            )

    with psycopg2.connect(connection_string) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            try:
                # reset role from charmed_read_database_owner to relation user first
                cursor.execute("RESET ROLE;")

                cursor.execute("CREATE TABLE test_table_2 (id SERIAL PRIMARY KEY, data TEXT);")
                assert False, "Able to write to charmed_read_database with charmed_read role"
            except psycopg2.errors.InsufficientPrivilege:
                pass

    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
    )
    await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")


@pytest.mark.abort_on_fail
async def test_charmed_dml_role(ops_test: OpsTest):
    """Test the charmed_dml role."""
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
        "database-name": "charmed_dml_database",
        "extra-user-roles": "charmed_dml",
    })
    await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
    )

    connection_string = await build_connection_string(
        ops_test,
        DATA_INTEGRATOR_APP_NAME,
        "postgresql",
        database="charmed_dml_database",
    )

    with psycopg2.connect(connection_string) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test_table (id SERIAL PRIMARY KEY, data TEXT);")

            # reset role from charmed_dml_database_owner to relation user first
            cursor.execute("RESET ROLE;")

            cursor.execute("INSERT INTO test_table (data) VALUES ('test_data'), ('test_data_2');")

            cursor.execute("SELECT data FROM test_table;")
            data = sorted([row[0] for row in cursor.fetchall()])
            assert data == sorted(["test_data", "test_data_2"]), (
                "Unexpected data in charmed_dml_database with charmed_dml role"
            )

    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
    )
    await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")


@pytest.mark.abort_on_fail
async def test_can_read_data_on_replica(ops_test: OpsTest):
    """Test to ensure a relation user can read from a replica.

    login_hook does not activate on a replica, and thus db_admin has read privileges
    to the tables in the database. This test ensure this privilege exists
    """
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
        "database-name": "read_on_replica",
    })
    await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
    )

    connection_string = await build_connection_string(
        ops_test,
        DATA_INTEGRATOR_APP_NAME,
        "postgresql",
        database="read_on_replica",
    )

    with psycopg2.connect(connection_string) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test_table (id SERIAL PRIMARY KEY, data TEXT);")
            cursor.execute("INSERT INTO test_table (data) VALUES ('test_data'), ('test_data_2');")

            cursor.execute("SELECT data FROM test_table;")
            data = sorted([row[0] for row in cursor.fetchall()])
            assert data == sorted(["test_data", "test_data_2"]), (
                "Unexpected data in read_on_replica database"
            )

    replica_connection_string = await build_connection_string(
        ops_test,
        DATA_INTEGRATOR_APP_NAME,
        "postgresql",
        database="read_on_replica",
        read_only_endpoint=True,
    )

    with psycopg2.connect(replica_connection_string) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute("SELECT data FROM test_table;")
            data = sorted([row[0] for row in cursor.fetchall()])
            assert data == sorted(["test_data", "test_data_2"]), (
                "Unexpected data in read_on_replica database"
            )

            try:
                cursor.execute("CREATE TABLE test_table_2 (id SERIAL PRIMARY KEY, data TEXT);")
                assert False, "Able to create a table on a replica"
            except psycopg2.errors.ReadOnlySqlTransaction:
                pass

    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
    )
    await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
