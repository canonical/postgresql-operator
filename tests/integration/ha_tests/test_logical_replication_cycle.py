# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from asyncio import gather

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from integration.helpers import get_leader_unit
from integration.new_relations.helpers import build_connection_string

DATABASE_APP_NAME = "postgresql"
SECOND_DATABASE_APP_NAME = "postgresql2"
THIRD_DATABASE_APP_NAME = "postgresql3"

DATA_INTEGRATOR_APP_NAME = "data-integrator"
SECOND_DATA_INTEGRATOR_APP_NAME = "data-integrator2"
THIRD_DATA_INTEGRATOR_APP_NAME = "data-integrator3"
DATA_INTEGRATOR_RELATION = "postgresql"

DATABASE_APP_CONFIG = {"profile": "testing"}

TESTING_DATABASE = "testdb"


@pytest.mark.abort_on_fail
async def test_cycle_detection_three_clusters(ops_test: OpsTest, charm):
    # Deploy three PostgreSQL clusters and three data-integrators (to create tables)
    await gather(
        ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            config=DATABASE_APP_CONFIG,
        ),
        ops_test.model.deploy(
            charm,
            application_name=SECOND_DATABASE_APP_NAME,
            num_units=1,
            config=DATABASE_APP_CONFIG,
        ),
        ops_test.model.deploy(
            charm,
            application_name=THIRD_DATABASE_APP_NAME,
            num_units=1,
            config=DATABASE_APP_CONFIG,
        ),
        ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=DATA_INTEGRATOR_APP_NAME,
            num_units=1,
            channel="latest/stable",
            config={"database-name": TESTING_DATABASE},
        ),
        ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=SECOND_DATA_INTEGRATOR_APP_NAME,
            num_units=1,
            channel="latest/stable",
            config={"database-name": TESTING_DATABASE},
        ),
        ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=THIRD_DATA_INTEGRATOR_APP_NAME,
            num_units=1,
            channel="latest/stable",
            config={"database-name": TESTING_DATABASE},
        ),
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, SECOND_DATABASE_APP_NAME, THIRD_DATABASE_APP_NAME],
        status="active",
        timeout=2500,
        raise_on_error=False,
    )

    # Integrate data-integrators for table creation
    async with ops_test.fast_forward():
        await gather(
            ops_test.model.integrate(DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
            ops_test.model.integrate(SECOND_DATABASE_APP_NAME, SECOND_DATA_INTEGRATOR_APP_NAME),
            ops_test.model.integrate(THIRD_DATABASE_APP_NAME, THIRD_DATA_INTEGRATOR_APP_NAME),
        )
        await ops_test.model.wait_for_idle(status="active", timeout=600)

    await _create_test_table(
        ops_test, DATA_INTEGRATOR_APP_NAME, TESTING_DATABASE, "public.test_cycle"
    )
    await _create_test_table(
        ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, TESTING_DATABASE, "public.test_cycle"
    )
    await _create_test_table(
        ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, TESTING_DATABASE, "public.test_cycle"
    )

    print("A -> B subscription")
    await ops_test.model.integrate(
        f"{DATABASE_APP_NAME}:logical-replication-offer",
        f"{SECOND_DATABASE_APP_NAME}:logical-replication",
    )
    await ops_test.model.wait_for_idle(status="active", timeout=600)

    pg2_config = DATABASE_APP_CONFIG.copy()
    pg2_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_cycle"],
    })
    await ops_test.model.applications[SECOND_DATABASE_APP_NAME].set_config(pg2_config)

    print("B -> C subscription")
    await ops_test.model.integrate(
        f"{SECOND_DATABASE_APP_NAME}:logical-replication-offer",
        f"{THIRD_DATABASE_APP_NAME}:logical-replication",
    )
    await ops_test.model.wait_for_idle(status="active", timeout=600)

    pg3_config = DATABASE_APP_CONFIG.copy()
    pg3_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_cycle"],
    })
    await ops_test.model.applications[THIRD_DATABASE_APP_NAME].set_config(pg3_config)

    print("Attempt C -> A subscription should be blocked due to cycle detection")
    await ops_test.model.integrate(
        f"{THIRD_DATABASE_APP_NAME}:logical-replication-offer",
        f"{DATABASE_APP_NAME}:logical-replication",
    )

    pg1_config = DATABASE_APP_CONFIG.copy()
    pg1_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_cycle"],
    })
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(pg1_config)

    # Expect leader of A to go into blocked state
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    await ops_test.model.block_until(lambda: leader_unit.workload_status == "blocked")


async def _create_test_table(
    ops_test: OpsTest, data_integrator_app_name: str, database: str, qualified_table: str
) -> None:
    connection_string = await build_connection_string(
        ops_test,
        data_integrator_app_name,
        DATA_INTEGRATOR_RELATION,
        database=database,
    )
    connection = None
    try:
        for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(3), reraise=True):
            with attempt:
                connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        with connection.cursor() as cursor:
            schema, table = qualified_table.split(".")
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} (test_column text);")
    finally:
        if connection is not None:
            connection.close()
