#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
    check_roles_and_their_permissions,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    relations,
)

logger = logging.getLogger(__name__)

DATABASE_NAME = "test"
RELATION_ENDPOINT = "postgresql"


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm) -> None:
    """Deploy and relate the charms."""
    reset_relation = False
    if DATABASE_APP_NAME not in ops_test.model.applications:
        logger.info("Deploying database charm")
        await ops_test.model.deploy(charm, config={"profile": "testing"}, num_units=2)
    else:
        logger.info("Dropping test databases from already deployed database charm")
        primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
        connection = None
        try:
            host = get_unit_address(ops_test, primary)
            password = await get_password(ops_test, database_app_name=DATABASE_APP_NAME)
            connection = db_connect(host, password)
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS {DATABASE_NAME};")
                cursor.execute(f"DROP DATABASE IF EXISTS {DATABASE_NAME}_2;")
        finally:
            if connection is not None:
                connection.close()
        reset_relation = True
    if DATA_INTEGRATOR_APP_NAME not in ops_test.model.applications:
        logger.info("Deploying data integrator charm")
        await ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME, config={"database-name": DATABASE_NAME}
        )
    else:
        logger.info("Resetting extra user roles in already deployed data integrator charm")
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "extra-user-roles": ""
        })
        reset_relation = True
    existing_relations = relations(ops_test, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME)
    if reset_relation and existing_relations:
        logger.info("Removing existing relation between charms")
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].remove_relation(
            f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
        )
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
        logger.info("Adding relation between charms")
        await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    if not existing_relations:
        logger.info("Adding relation between charms")
        await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )


@pytest.mark.abort_on_fail
async def test_permissions(ops_test: OpsTest) -> None:
    """Test that the relation user is automatically escalated to the database owner user."""
    await check_roles_and_their_permissions(ops_test, RELATION_ENDPOINT, DATABASE_APP_NAME)


@pytest.mark.abort_on_fail
async def test_remove_and_reestablish_relation(ops_test: OpsTest) -> None:
    """Test that the relation can be removed and re-added without issues."""
    logger.info("Removing existing relation between charms")
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].remove_relation(
        f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
    )
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked"),
            ops_test.model.block_until(
                lambda: len(relations(ops_test, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME)) == 0
            ),
        )

    logger.info("Dropping test table to recreate it")
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    connection = None
    try:
        host = get_unit_address(ops_test, primary)
        password = await get_password(ops_test, database_app_name=DATABASE_APP_NAME)
        connection = db_connect(host, password, database=DATABASE_NAME)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE test_table;")
    finally:
        if connection is not None:
            connection.close()

    logger.info("Adding relation between charms")
    await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )

    await check_roles_and_their_permissions(ops_test, RELATION_ENDPOINT, DATABASE_APP_NAME)

    logger.info("Removing existing relation between charms")
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].remove_relation(
        f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
    )
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked"),
            ops_test.model.block_until(
                lambda: len(relations(ops_test, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME)) == 0
            ),
        )

    logger.info("Dropping test database to recreate it")
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    connection = None
    try:
        host = get_unit_address(ops_test, primary)
        password = await get_password(ops_test, database_app_name=DATABASE_APP_NAME)
        connection = db_connect(host, password)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE {DATABASE_NAME};")
    finally:
        if connection is not None:
            connection.close()

    logger.info("Adding relation between charms")
    await ops_test.model.relate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )

    await check_roles_and_their_permissions(ops_test, RELATION_ENDPOINT, DATABASE_APP_NAME)


@pytest.mark.abort_on_fail
async def test_database_creation_permissions(ops_test: OpsTest) -> None:
    """Test that the database creation permissions are correctly set for the extra user role."""
    logger.info("Removing existing relation between charms")
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].remove_relation(
        f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
    )
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked"),
            ops_test.model.block_until(
                lambda: len(relations(ops_test, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME)) == 0
            ),
        )

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
            cursor.execute(f"CREATE DATABASE {DATABASE_NAME}_2;")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("CREATE TABLE test_table_2 (id INTEGER);")
    finally:
        if connection is not None:
            connection.close()
