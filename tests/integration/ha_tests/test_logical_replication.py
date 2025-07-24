import json
from asyncio import gather

import psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from integration.ha_tests.helpers import get_cluster_roles
from integration.helpers import CHARM_BASE, get_leader_unit
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

TIMEOUT = 20 * 60


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm):
    await gather(
        ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=3,
            base=CHARM_BASE,
            config=DATABASE_APP_CONFIG,
        ),
        ops_test.model.deploy(
            charm,
            application_name=SECOND_DATABASE_APP_NAME,
            num_units=3,
            base=CHARM_BASE,
            config=DATABASE_APP_CONFIG,
        ),
        ops_test.model.deploy(
            charm,
            application_name=THIRD_DATABASE_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
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
        # There can be error spikes during PostgreSQL deployment, that are not related to Logical Replication
        raise_on_error=False,
    )
    async with ops_test.fast_forward():
        await gather(
            ops_test.model.integrate(DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
            ops_test.model.integrate(SECOND_DATABASE_APP_NAME, SECOND_DATA_INTEGRATOR_APP_NAME),
            ops_test.model.integrate(THIRD_DATABASE_APP_NAME, THIRD_DATA_INTEGRATOR_APP_NAME),
            ops_test.model.integrate(
                f"{DATABASE_APP_NAME}:logical-replication-offer",
                f"{SECOND_DATABASE_APP_NAME}:logical-replication",
            ),
        )
        await ops_test.model.wait_for_idle(status="active", timeout=TIMEOUT)


@pytest.mark.abort_on_fail
async def test_pg2_publisher_error(ops_test: OpsTest):
    await _create_test_table(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME)

    pg2_config = DATABASE_APP_CONFIG.copy()
    pg2_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_table"]
    })
    await ops_test.model.applications[SECOND_DATABASE_APP_NAME].set_config(pg2_config)

    await _wait_for_leader_on_blocked(ops_test, SECOND_DATABASE_APP_NAME)


@pytest.mark.abort_on_fail
async def test_pg3_local_error(ops_test: OpsTest):
    pg3_config = DATABASE_APP_CONFIG.copy()
    pg3_config["logical_replication_subscription_request"] = json.dumps({
        "bad_database": ["bad_format"]
    })
    await ops_test.model.applications[THIRD_DATABASE_APP_NAME].set_config(pg3_config)
    await _wait_for_leader_on_blocked(ops_test, THIRD_DATABASE_APP_NAME)

    await ops_test.model.integrate(
        f"{DATABASE_APP_NAME}:logical-replication-offer",
        f"{THIRD_DATABASE_APP_NAME}:logical-replication",
    )
    await _wait_for_leader_on_blocked(ops_test, THIRD_DATABASE_APP_NAME)

    pg3_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_table2"]
    })
    await ops_test.model.applications[THIRD_DATABASE_APP_NAME].set_config(pg3_config)
    await _wait_for_leader_on_blocked(ops_test, THIRD_DATABASE_APP_NAME)


@pytest.mark.abort_on_fail
async def test_resolve_errors(ops_test: OpsTest):
    await gather(
        _create_test_table(ops_test, DATA_INTEGRATOR_APP_NAME),
        _create_test_table(ops_test, DATA_INTEGRATOR_APP_NAME, "test_table2"),
        _create_test_table(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "test_table2"),
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active")


@pytest.mark.abort_on_fail
async def test_replication(ops_test: OpsTest):
    await gather(
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "first"),
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "first", "test_table2"),
    )

    await gather(
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "first"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "first", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_switchover(ops_test: OpsTest):
    publisher_leader = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    publisher_roles = await get_cluster_roles(ops_test, publisher_leader.name)
    publisher_candidate = ops_test.model.units[publisher_roles["sync_standbys"][0]]
    action = await publisher_candidate.run_action("promote-to-primary", scope="unit", force=True)
    await action.wait()

    subscriber_leader = await get_leader_unit(ops_test, SECOND_DATABASE_APP_NAME)
    subscriber_roles = await get_cluster_roles(ops_test, subscriber_leader.name)
    subscriber_candidate = ops_test.model.units[subscriber_roles["sync_standbys"][0]]
    action = await subscriber_candidate.run_action("promote-to-primary", scope="unit", force=True)
    await action.wait()

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=TIMEOUT)


@pytest.mark.abort_on_fail
async def test_replication_after_switchover(ops_test: OpsTest):
    await gather(
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "second"),
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "second", "test_table2"),
    )
    await gather(
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "second"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "second", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_pg3_extend_subscription(ops_test: OpsTest):
    await _create_test_table(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME)

    pg3_config = DATABASE_APP_CONFIG.copy()
    pg3_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_table", "public.test_table2"]
    })
    await ops_test.model.applications[THIRD_DATABASE_APP_NAME].set_config(pg3_config)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=TIMEOUT)

    await gather(
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "second"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "second", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_pg2_change_subscription(ops_test: OpsTest):
    await _create_test_table(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "test_table2")

    pg2_config = DATABASE_APP_CONFIG.copy()
    pg2_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_table2"]
    })
    await ops_test.model.applications[SECOND_DATABASE_APP_NAME].set_config(pg2_config)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=TIMEOUT)

    await gather(
        _check_test_data(
            ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "second"
        ),  # old data will be left behind
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "second", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_replication_after_subscriptions_changes(ops_test: OpsTest):
    await gather(
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "third"),
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "third", "test_table2"),
    )
    await gather(
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "second"),
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "third", "test_table2"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "third"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "third", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_pg2_dynamic_error(ops_test: OpsTest):
    pg2_config = DATABASE_APP_CONFIG.copy()
    pg2_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_table", "public.test_table2"]
    })
    await ops_test.model.applications[SECOND_DATABASE_APP_NAME].set_config(pg2_config)
    await _wait_for_leader_on_blocked(ops_test, SECOND_DATABASE_APP_NAME)


@pytest.mark.abort_on_fail
async def test_replication_during_dynamic_error(ops_test: OpsTest):
    await gather(
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "fourth"),
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "fourth", "test_table2"),
    )
    await gather(
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "second"),
        _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "fourth", "test_table2"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "fourth"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "fourth", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_pg2_resolve_dynamic_error(ops_test: OpsTest):
    connection_string = await build_connection_string(
        ops_test,
        SECOND_DATA_INTEGRATOR_APP_NAME,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    with (
        psycopg2.connect(connection_string) as connection,
        connection.cursor() as cursor,
    ):
        connection.autocommit = True
        cursor.execute("DELETE FROM test_table;")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=TIMEOUT)
    await _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "fourth")


@pytest.mark.abort_on_fail
async def test_pg2_remove(ops_test: OpsTest):
    await ops_test.model.remove_application(SECOND_DATA_INTEGRATOR_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(SECOND_DATABASE_APP_NAME, block_until_done=True)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    connection_string = await build_connection_string(
        ops_test,
        DATA_INTEGRATOR_APP_NAME,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(1) FROM pg_replication_slots where slot_type='logical';")
        assert cursor.fetchone()[0] == 1, (
            "unused replication slot should be removed in the publisher cluster"
        )


@pytest.mark.abort_on_fail
async def test_replication_after_pg2_removal(ops_test: OpsTest):
    await gather(
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "fifth"),
        _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "fifth", "test_table2"),
    )
    await gather(
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "fifth"),
        _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "fifth", "test_table2"),
    )


@pytest.mark.abort_on_fail
async def test_pg3_remove_relation(ops_test: OpsTest):
    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:logical-replication-offer",
        f"{THIRD_DATABASE_APP_NAME}:logical-replication",
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=TIMEOUT)
    connection_string = await build_connection_string(
        ops_test,
        THIRD_DATA_INTEGRATOR_APP_NAME,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(1) FROM pg_subscription;")
        assert cursor.fetchone()[0] == 0, (
            "unused PostgreSQL subscription should be removed in the subscriber cluster"
        )


async def _wait_for_leader_on_blocked(ops_test: OpsTest, app_name: str) -> None:
    leader_unit = await get_leader_unit(ops_test, app_name)
    await ops_test.model.block_until(lambda: leader_unit.workload_status == "blocked")


async def _create_test_table(
    ops_test: OpsTest, data_integrator_app_name: str, table_name: str = "test_table"
) -> None:
    connection_string = await build_connection_string(
        ops_test,
        data_integrator_app_name,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    connection = None
    try:
        for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(3), reraise=True):
            with attempt:
                connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE {table_name} (test_column text);")
    finally:
        if connection is not None:
            connection.close()


async def _insert_test_data(
    ops_test: OpsTest, data_integrator_app_name: str, data: str, table_name: str = "test_table"
) -> None:
    connection_string = await build_connection_string(
        ops_test,
        data_integrator_app_name,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    connection = None
    try:
        for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(3), reraise=True):
            with attempt:
                connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table_name} (test_column) VALUES (%s);",
                (data,),
            )
    finally:
        if connection is not None:
            connection.close()


async def _check_test_data(
    ops_test: OpsTest, data_integrator_app_name: str, data: str, table_name: str = "test_table"
) -> bool:
    connection_string = await build_connection_string(
        ops_test,
        data_integrator_app_name,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    connection = None
    try:
        for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(3), reraise=True):
            with attempt:
                connection = psycopg2.connect(connection_string)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT EXISTS (SELECT 1 FROM {table_name} WHERE test_column = %s);",
                (data,),
            )
            return cursor.fetchone()[0]
    finally:
        if connection is not None:
            connection.close()
