#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
import secrets
import string

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..ha_tests.helpers import (
    remove_unit_force,
    storage_id, is_storage_exists, add_unit_with_storage, is_postgresql_ready, get_any_deatached_storage
)
from ..helpers import CHARM_SERIES, DATABASE_APP_NAME
from .helpers import build_connection_string
from ..juju_ import juju_major_version

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
APP_NAMES = [DATABASE_APP_NAME, APPLICATION_APP_NAME]
FIRST_DATABASE_RELATION_NAME = "first-database"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_charms(ops_test: OpsTest, charm):
    """Deploy both charms (application and database) to use in the tests."""
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=APPLICATION_APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
            ),
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
                config={"profile": "testing"},
            ),
        )

        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", timeout=3000)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest, charm):
    """Test that check relation data."""
    async with ops_test.fast_forward():
        await asyncio.gather(

        )

        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", timeout=3000)

        # Relate with client-app, wait-for-ready, check relation data, remove-relation
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        primary_connection_string = await build_connection_string(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
        )

        with psycopg2.connect(primary_connection_string) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                # Check that it's possible to write and read data from the database that
                # was created for the application.
                cursor.execute("DROP TABLE IF EXISTS test;")
                cursor.execute("CREATE TABLE test(data TEXT);")
                cursor.execute("INSERT INTO test(data) VALUES('some data');")
                cursor.execute("SELECT data FROM test;")
                data = cursor.fetchone()
                assert data[0] == "some data"
        connection.close()

        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database",
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}",
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
        storage_id_str = storage_id(ops_test, primary_name)

        if juju_major_version == 2:
            await remove_unit_force(ops_test, primary_name)
        else:
            await ops_test.model.destroy_unit(
                primary_name, force=True, destroy_storage=False, max_wait=1500
            )

        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id_str)

        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        await add_unit_with_storage(ops_test, DATABASE_APP_NAME, garbage_storage)

        # Relate with client-app 2nd time, wait-for-ready, check relation data
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        primary_connection_string = await build_connection_string(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
        )
        with psycopg2.connect(primary_connection_string) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT data FROM test;")
                data = cursor.fetchone()
                logger.info("verifying some data exist")
                assert data[0] == "some data"
        connection.close()

        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database",
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}",
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        # Repeat re-relation using all available relation options (e.g. legacy_roles)
        await ops_test.model.applications[APPLICATION_APP_NAME].set_config({
            "legacy_roles": "true",
        })
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_APP_NAME], status="active", timeout=1000
        )

        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        primary_connection_string = await build_connection_string(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
        )

        try:
            connection = psycopg2.connect(primary_connection_string)
            connection.autocommit = True
            cursor = connection.cursor()
            random_name = f"test_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(10))}"
            cursor.execute(f"CREATE DATABASE {random_name};")
            cursor.execute(f"DROP DATABASE {random_name};")
        except psycopg2.errors.InsufficientPrivilege as e:
            assert (
                False
            ), "failed to connect to or run a statement in the following database"
        finally:
            if connection is not None:
                connection.close()
