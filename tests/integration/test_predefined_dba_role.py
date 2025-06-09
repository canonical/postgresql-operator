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
    check_connected_user,
)
from .new_relations.helpers import build_connection_string

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm: str):
    """Deploy the postgresql charm along with data integrator charm."""
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

        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")


@pytest.mark.abort_on_fail
async def test_charmed_dba_role(ops_test: OpsTest):
    """Test the DBA predefined role."""
    await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
        "database-name": "charmed_dba_database",
        "extra-user-roles": "charmed_dba",
    })
    await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
    )

    action = await ops_test.model.units[f"{DATA_INTEGRATOR_APP_NAME}/0"].run_action(
        action_name="get-credentials"
    )
    result = await action.wait()
    data_integrator_credentials = result.results
    username = data_integrator_credentials["postgresql"]["username"]

    for read_write_endpoint in [True, False]:
        connection_string = await build_connection_string(
            ops_test,
            DATA_INTEGRATOR_APP_NAME,
            "postgresql",
            database="charmed_dba_database",
            read_only_endpoint=(not read_write_endpoint),
        )
        connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                instance = "primary" if read_write_endpoint else "replica"
                logger.info(f"Resetting the user to the {username} user in the {instance}")
                cursor.execute("RESET ROLE;")
                check_connected_user(cursor, username, username, primary=read_write_endpoint)
                logger.info(f"Testing escalation to the rewind user in the {instance}")
                cursor.execute("SELECT set_user('rewind'::TEXT);")
                check_connected_user(cursor, username, "rewind", primary=read_write_endpoint)
                logger.info(f"Resetting the user to the {username} user in the {instance}")
                cursor.execute("SELECT reset_user();")
                check_connected_user(cursor, username, username, primary=read_write_endpoint)
                logger.info(f"Testing escalation to the operator user in the {instance}")
                cursor.execute("SELECT set_user_u('operator'::TEXT);")
                check_connected_user(cursor, username, "operator", primary=read_write_endpoint)
                logger.info(f"Resetting the user to the {username} user in the {instance}")
                cursor.execute("SELECT reset_user();")
                check_connected_user(cursor, username, username, primary=read_write_endpoint)
        finally:
            if connection is not None:
                connection.close()
