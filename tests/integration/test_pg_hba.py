#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import re
from time import sleep

import psycopg2
import pytest

from .adapters import JujuFixture
from .jubilant_helpers import (
    DATABASE_APP_NAME,
    db_connect,
    get_primary,
    get_unit_address,
)

logger = logging.getLogger(__name__)

DATA_INTEGRATOR_APP_NAME = "data-integrator"
FIRST_DATABASE = "test_1"
SECOND_DATABASE = "test_2"
FIRST_RELATION_USER = "relation_id_0"
SECOND_RELATION_USER = "relation_id_1"
PASSWORD = "test-password"


@pytest.mark.abort_on_fail
def test_pg_hba(juju: JujuFixture, charm):
    with juju.ext.fast_forward():
        logger.info("Deploying charms")
        if DATABASE_APP_NAME not in juju.ext.model.applications:
            juju.ext.model.deploy(
                charm,
                num_units=2,
                config={"profile": "testing"},
            )
        if DATA_INTEGRATOR_APP_NAME not in juju.ext.model.applications:
            juju.ext.model.deploy(
                DATA_INTEGRATOR_APP_NAME,
                config={"database-name": FIRST_DATABASE, "extra-user-roles": "SUPERUSER"},
            )

        logger.info("Adding relation between charms")
        relations = [
            relation
            for relation in juju.ext.model.applications[DATABASE_APP_NAME].relations
            if not relation.is_peer
            and f"{relation.requires.application_name}:{relation.requires.name}"
            == f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        ]
        if not relations:
            juju.ext.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)

        juju.ext.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active", timeout=1000
        )

        primary = get_primary(juju, f"{DATABASE_APP_NAME}/0")
        address = get_unit_address(juju, primary)
        data_integrator_unit = juju.ext.model.applications[DATA_INTEGRATOR_APP_NAME].units[0]
        action = data_integrator_unit.run_action(action_name="get-credentials")
        result = action.wait()
        credentials = result.results
        connection = None
        try:
            connection = db_connect(
                host=address,
                password=credentials["postgresql"]["password"],
                username=credentials["postgresql"]["username"],
                database=FIRST_DATABASE,
            )
            connection.autocommit = True
            with connection.cursor() as cursor:
                logger.info("Dropping database objects from the previous test run (if any)")
                cursor.execute("RESET ROLE;")
                cursor.execute(f"DROP USER IF EXISTS {FIRST_RELATION_USER};")
                cursor.execute(
                    f"SELECT datname FROM pg_database WHERE datname='{SECOND_DATABASE}';"
                )
                if cursor.fetchone() is not None:
                    cursor.execute(
                        f"REVOKE ALL ON DATABASE {SECOND_DATABASE} FROM {SECOND_RELATION_USER};"
                    )
                cursor.execute(f"DROP USER IF EXISTS {SECOND_RELATION_USER};")
                cursor.execute(f"DROP DATABASE IF EXISTS {SECOND_DATABASE};")
                cursor.execute("DROP SCHEMA IF EXISTS test;")

                logger.info("Creating database objects needed for the test")
                cursor.execute(
                    f"CREATE USER {FIRST_RELATION_USER} WITH LOGIN SUPERUSER ENCRYPTED PASSWORD '{PASSWORD}';"
                )
                cursor.execute(
                    f"CREATE USER {SECOND_RELATION_USER} WITH LOGIN ENCRYPTED PASSWORD '{PASSWORD}';"
                )
                cursor.execute(f"CREATE DATABASE {SECOND_DATABASE};")
                cursor.execute(
                    f"GRANT CONNECT ON DATABASE {SECOND_DATABASE} TO {SECOND_RELATION_USER};"
                )
                cursor.execute("CREATE SCHEMA test;")
        finally:
            if connection:
                connection.close()

        sleep(90)

        for unit in juju.ext.model.applications[DATABASE_APP_NAME].units:
            try:
                address = get_unit_address(juju, unit.name)

                logger.info(
                    f"Checking that the user {FIRST_RELATION_USER} can connect to the database {FIRST_DATABASE} on {unit.name}"
                )
                with (
                    db_connect(
                        host=address,
                        password=PASSWORD,
                        username=FIRST_RELATION_USER,
                        database=FIRST_DATABASE,
                    ) as connection,
                    connection.cursor() as cursor,
                ):
                    # Check the version that the application received is the same on the
                    # database server.
                    cursor.execute("SELECT version();")
                    data = cursor.fetchone()[0].split(" ")[1]

                    # Get the version of the database and compare with the information that
                    # was retrieved directly from the database.
                    assert credentials["postgresql"]["version"] == data
                connection.close()

                logger.info(
                    f"Checking that the user {SECOND_RELATION_USER} can connect to the database {SECOND_DATABASE} on {unit.name}"
                )
                with (
                    db_connect(
                        host=address,
                        password=PASSWORD,
                        username=SECOND_RELATION_USER,
                        database=SECOND_DATABASE,
                    ) as connection,
                    connection.cursor() as cursor,
                ):
                    # Check the version that the application received is the same on the
                    # database server.
                    cursor.execute("SELECT VERSION();")

                    # Get the version of the database and compare with the information that
                    # was retrieved directly from the database.
                    assert credentials["postgresql"]["version"] == data

                logger.info(
                    f"Checking that the user {SECOND_RELATION_USER} cannot connect to the database {FIRST_DATABASE} on {unit.name}"
                )
                with db_connect(
                    host=address,
                    password=PASSWORD,
                    username=SECOND_RELATION_USER,
                    database=FIRST_DATABASE,
                ) as connection:
                    assert False, (
                        f"User {SECOND_RELATION_USER} should not be able to connect to the database {FIRST_DATABASE}"
                    )
            except psycopg2.OperationalError as e:
                if (
                    re.search(
                        f'^(connection to server at \\").*(\\", port 5432).*(failed: FATAL:  no pg_hba.conf entry for host \\").*(\\", user \\"{SECOND_RELATION_USER}\\", database \\"{FIRST_DATABASE}\\", no encryption)$',
                        str(e),
                    )
                    is None
                ):
                    raise
            finally:
                if connection:
                    connection.close()
