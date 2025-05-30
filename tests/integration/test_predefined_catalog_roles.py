#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
)
from .new_relations.helpers import (
    build_connection_string,
    get_application_relation_data,
    get_juju_secret,
)

logger = logging.getLogger(__name__)

RELATION_ENDPOINT = "database"


@pytest.mark.abort_on_fail
async def test_predefined_catalog_roles(ops_test: OpsTest, charm) -> None:
    """Test the audit plugin."""
    async with ops_test.fast_forward():
        logger.info("Deploying charms")
        if DATABASE_APP_NAME not in ops_test.model.applications:
            await ops_test.model.deploy(charm, config={"profile": "testing"}, num_units=2)
        if APPLICATION_NAME not in ops_test.model.applications:
            await ops_test.model.deploy(APPLICATION_NAME)
        logger.info("Adding relation between charms")
        relations = [
            relation
            for relation in ops_test.model.applications[DATABASE_APP_NAME].relations
            if not relation.is_peer
            and f"{relation.requires.application_name}:{relation.requires.name}"
            == f"{APPLICATION_NAME}:{RELATION_ENDPOINT}"
        ]
        if not relations:
            await ops_test.model.relate(
                f"{APPLICATION_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
            )
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[APPLICATION_NAME, DATABASE_APP_NAME], status="active"
            )

        logger.info(
            "Checking that the relation user is automatically escalated to the database owner user"
        )
        secret_uri = await get_application_relation_data(
            ops_test, APPLICATION_NAME, RELATION_ENDPOINT, "secret-user"
        )
        secret_data = await get_juju_secret(ops_test, secret_uri)
        username = secret_data["username"]
        connection_string = await build_connection_string(
            ops_test, APPLICATION_NAME, RELATION_ENDPOINT
        )
        connection = None
        try:
            connection = psycopg2.connect(connection_string)
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user,current_user;")
                result = cursor.fetchone()
                if result is not None:
                    assert result[0] == username, (
                        "The session user should be the relation user in the primary"
                    )
                    assert (
                        result[1]
                        == f"{APPLICATION_NAME.replace('-', '_')}_{RELATION_ENDPOINT}_owner"
                    ), "The current user should be the database owner user in the primary"
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
                assert len(result) == 1, "The owner user should be able to read the data"
        finally:
            if connection is not None:
                connection.close()

        logger.info("Checking that the relation user can read data from the database")
        connection_string = await build_connection_string(
            ops_test, APPLICATION_NAME, RELATION_ENDPOINT, read_only_endpoint=True
        )
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
