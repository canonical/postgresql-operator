#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import DATABASE_APP_NAME

logger = logging.getLogger(__name__)

DATA_INTEGRATOR_APP_NAME = "data-integrator"
RELATION_ENDPOINT = "postgresql"


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm) -> None:
    """Deploy and relate the charms."""
    logger.info("Deploying charms")
    if DATABASE_APP_NAME not in ops_test.model.applications:
        await ops_test.model.deploy(charm, config={"profile": "testing"}, num_units=2)
    reset_relation = False
    if DATA_INTEGRATOR_APP_NAME not in ops_test.model.applications:
        await ops_test.model.deploy(DATA_INTEGRATOR_APP_NAME, config={"database-name": "test"})
    else:
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "extra-user-roles": ""
        })
        reset_relation = True
    logger.info("Adding relation between charms")
    relations = [
        relation
        for relation in ops_test.model.applications[DATABASE_APP_NAME].relations
        if not relation.is_peer and relation.requires.application_name == DATA_INTEGRATOR_APP_NAME
    ]
    if reset_relation and relations:
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].remove_relation(
            f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
        )
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
        await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    if not relations:
        await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )


@pytest.mark.abort_on_fail
async def test_permissions(ops_test: OpsTest) -> None:
    """Test that the relation user is automatically escalated to the database owner user."""
    logger.info(
        "Checking that the relation user is automatically escalated to the database owner user"
    )
    action = await ops_test.model.units[f"{DATA_INTEGRATOR_APP_NAME}/0"].run_action(
        action_name="get-credentials"
    )
    result = await action.wait()
    data_integrator_credentials = result.results
    username = data_integrator_credentials[RELATION_ENDPOINT]["username"]
    uris = data_integrator_credentials[RELATION_ENDPOINT]["uris"]
    connection = None
    try:
        connection = psycopg2.connect(uris)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user,current_user;")
            result = cursor.fetchone()
            if result is not None:
                assert result[0] == username, (
                    "The session user should be the relation user in the primary"
                )
                assert result[1] == "test_owner", (
                    "The current user should be the database owner user in the primary"
                )
            else:
                assert False, "No result returned from the query"
            logger.info("Creating a test table and inserting data")
            cursor.execute("DROP TABLE IF EXISTS test_table;")
            cursor.execute("CREATE TABLE test_table (id INTEGER);")
            logger.info("Inserting data into the test table")
            cursor.execute("INSERT INTO test_table(id) VALUES(1);")
            logger.info("Reading data from the test table")
            cursor.execute("SELECT * FROM test_table;")
            result = cursor.fetchall()
            assert len(result) == 1, "The database owner user should be able to read the data"

            logger.info("Checking that the database owner user can't create a database")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("CREATE DATABASE test_2;")

            logger.info("Checking that the relation user can't create a table")
            cursor.execute("DROP TABLE IF EXISTS test_table_2;")
            cursor.execute("RESET ROLE;")
            cursor.execute("SELECT session_user,current_user;")
            result = cursor.fetchone()
            if result is not None:
                assert result[0] == username, (
                    "The session user should be the relation user in the primary"
                )
                assert result[1] == username, (
                    "The current user should be the relation user in the primary"
                )
            else:
                assert False, "No result returned from the query"
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("CREATE TABLE test_table_2 (id INTEGER);")
    finally:
        if connection is not None:
            connection.close()

    logger.info("Checking that the relation user can read data from the database")
    connection_string = f"host={data_integrator_credentials[RELATION_ENDPOINT]['read-only-endpoints'].split(':')[0]} dbname={data_integrator_credentials[RELATION_ENDPOINT]['database']} user={username} password={data_integrator_credentials[RELATION_ENDPOINT]['password']}"
    connection = None
    try:
        connection = psycopg2.connect(connection_string)
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user,current_user;")
            result = cursor.fetchone()
            if result is not None:
                assert result[0] == username, (
                    "The session user should be the relation user in the replica"
                )
                assert result[1] == username, (
                    "The current user should be the relation user in the replica"
                )
            else:
                assert False, "No result returned from the query"
            logger.info("Reading data from the test table")
            cursor.execute("SELECT * FROM test_table;")
            result = cursor.fetchall()
            assert len(result) == 1, "The relation user should be able to read the data"
    finally:
        if connection is not None:
            connection.close()


@pytest.mark.abort_on_fail
async def test_database_creation_permissions(ops_test: OpsTest) -> None:
    """Test that the database creation permissions are correctly set for the extra user role."""
    logger.info("Removing relation")
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].remove_relation(
        f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")

    logger.info("Configuring data integrator charm for database creation extra user role")
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
        "extra-user-roles": "charmed_databases_owner"
    })

    logger.info("Adding relation between charms")
    await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )

    logger.info("Checking that the database owner user can create a database")
    action = await ops_test.model.units[f"{DATA_INTEGRATOR_APP_NAME}/0"].run_action(
        action_name="get-credentials"
    )
    result = await action.wait()
    data_integrator_credentials = result.results
    username = data_integrator_credentials[RELATION_ENDPOINT]["username"]
    uris = data_integrator_credentials[RELATION_ENDPOINT]["uris"]
    connection = None
    try:
        connection = psycopg2.connect(uris)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user,current_user;")
            result = cursor.fetchone()
            if result is not None:
                assert result[0] == username, (
                    "The session user should be the relation user in the primary"
                )
                assert result[1] == "charmed_databases_owner", (
                    "The current user should be the charmed_databases_owner user in the primary"
                )
            else:
                assert False, "No result returned from the query"
            cursor.execute("CREATE DATABASE test_2;")
    finally:
        if connection is not None:
            connection.close()
