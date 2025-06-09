#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import psycopg2
import psycopg2.sql
import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE,
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)
from .new_relations.helpers import (
    build_connection_string,
    get_application_relation_data,
    get_juju_secret,
)

logger = logging.getLogger(__name__)

TIMEOUT = 15 * 60

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
            ops_test.model.deploy(
                DATA_INTEGRATOR_APP_NAME,
                application_name=f"{DATA_INTEGRATOR_APP_NAME}2",
                base=CHARM_BASE,
            ),
        )

        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT
        )
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, f"{DATA_INTEGRATOR_APP_NAME}2"],
            status="blocked",
            timeout=(5 * 60),
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

    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    primary_address = get_unit_address(ops_test, primary)
    operator_password = await get_password(ops_test, "operator")

    with db_connect(
        primary_address, operator_password, username="operator", database="charmed_read_database"
    ) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test_table (id SERIAL PRIMARY KEY, data TEXT);")
            cursor.execute("INSERT INTO test_table (data) VALUES ('test_data'), ('test_data_2');")

    connection_string = await build_connection_string(
        ops_test,
        DATA_INTEGRATOR_APP_NAME,
        "postgresql",
        database="charmed_read_database",
    )

    with psycopg2.connect(connection_string) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            logger.info("Checking that the charmed_read role can read from the database")
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
            logger.info("Checking that the charmed_read role cannot write to the database")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("CREATE TABLE test_table_2 (id INTEGER);")
    connection.close()

    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
    )
    await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")


@pytest.mark.abort_on_fail
async def test_charmed_dml_role(ops_test: OpsTest):
    """Test the charmed_dml role."""
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
        "database-name": "charmed_dml_database",
    })
    await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
    )

    await ops_test.model.applications[f"{DATA_INTEGRATOR_APP_NAME}2"].set_config({
        "database-name": "throwaway",
        "extra-user-roles": "charmed_dml",
    })
    await ops_test.model.add_relation(f"{DATA_INTEGRATOR_APP_NAME}2", DATABASE_APP_NAME)
    await ops_test.model.wait_for_idle(apps=[f"{DATA_INTEGRATOR_APP_NAME}2"], status="active")

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

            cursor.execute("INSERT INTO test_table (data) VALUES ('test_data'), ('test_data_2');")

            cursor.execute("SELECT data FROM test_table;")
            data = sorted([row[0] for row in cursor.fetchall()])
            assert data == sorted(["test_data", "test_data_2"]), (
                "Unexpected data in charmed_dml_database with charmed_dml role"
            )

    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    primary_address = get_unit_address(ops_test, primary)
    operator_password = await get_password(ops_test, "operator")

    secret_uri = await get_application_relation_data(
        ops_test,
        f"{DATA_INTEGRATOR_APP_NAME}2",
        "postgresql",
        "secret-user",
    )
    secret_data = await get_juju_secret(ops_test, secret_uri)
    data_integrator_2_user = secret_data["username"]
    data_integrator_2_password = secret_data["password"]

    with db_connect(primary_address, operator_password, username="operator") as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute(
                psycopg2.sql.SQL("GRANT connect ON DATABASE charmed_dml_database TO {};").format(
                    psycopg2.sql.Identifier(data_integrator_2_user)
                )
            )

    with (
        db_connect(
            primary_address,
            data_integrator_2_password,
            username=data_integrator_2_user,
            database="charmed_dml_database",
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("INSERT INTO test_table (data) VALUES ('test_data_3');")

    with db_connect(
        primary_address, operator_password, username="operator", database="charmed_dml_database"
    ) as connection:
        connection.autocommit = True

        with connection.cursor() as cursor:
            cursor.execute("SELECT data FROM test_table;")
            data = sorted([row[0] for row in cursor.fetchall()])
            assert data == sorted(["test_data", "test_data_2", "test_data_3"]), (
                "Unexpected data in charmed_read_database with charmed_read role"
            )

    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
    )
    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}2:postgresql"
    )
    await ops_test.model.wait_for_idle(
        apps=[DATA_INTEGRATOR_APP_NAME, f"{DATA_INTEGRATOR_APP_NAME}2"], status="blocked"
    )
