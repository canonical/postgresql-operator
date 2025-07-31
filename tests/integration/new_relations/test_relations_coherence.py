#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import secrets
import string
from asyncio import gather, sleep

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest

from constants import DATABASE_DEFAULT_NAME

from ..helpers import CHARM_BASE, DATABASE_APP_NAME
from .helpers import build_connection_string
from .test_new_relations_1 import DATA_INTEGRATOR_APP_NAME

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
APP_NAMES = [DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME]
FIRST_DATABASE_RELATION_NAME = "database"


@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest, charm):
    """Test that check relation data."""
    async with ops_test.fast_forward():
        await gather(
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=1,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            # Creating first time relation with user role
            # Base is ignored
            ops_test.model.deploy(DATA_INTEGRATOR_APP_NAME, series="noble"),
        )
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "database-name": DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
        })
        await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", timeout=1500)

        await sleep(90)  # Wait for pg_hba.conf to be updated.

        connection_string = await build_connection_string(
            ops_test,
            DATA_INTEGRATOR_APP_NAME,
            "postgresql",
            database=DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
        )

        connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        cursor = connection.cursor()
        try:
            random_name = (
                f"test_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(10))}"
            )
            cursor.execute(f"CREATE DATABASE {random_name};")
            assert False, "user role was able to create database"
        except psycopg2.errors.InsufficientPrivilege:
            pass
        finally:
            connection.close()

        with psycopg2.connect(connection_string) as connection:
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
            f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        )

        # Re-relation again with user role and checking write data
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "database-name": DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
            "extra-user-roles": "",
        })
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
        await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        for database in [
            DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
            DATABASE_DEFAULT_NAME,
        ]:
            logger.info(f"connecting to the following database: {database}")
            connection_string = await build_connection_string(
                ops_test, DATA_INTEGRATOR_APP_NAME, "postgresql", database=database
            )
            connection = None
            should_fail = database == DATABASE_DEFAULT_NAME
            try:
                with (
                    psycopg2.connect(connection_string) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute("SELECT data FROM test;")
                    data = cursor.fetchone()
                    assert data[0] == "some data"

                    # Write some data (it should fail in the default database name).
                    random_name = f"test_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(10))}"
                    cursor.execute(f"CREATE TABLE {random_name}(data TEXT);")
                    if should_fail:
                        assert False, (
                            f"failed to run a statement in the following database: {database}"
                        )
            except psycopg2.errors.InsufficientPrivilege as e:
                if not should_fail:
                    logger.exception(e)
                    assert False, (
                        f"failed to connect to or run a statement in the following database: {database}"
                    )
            except psycopg2.OperationalError as e:
                if not should_fail:
                    logger.exception(e)
            finally:
                if connection is not None:
                    connection.close()
