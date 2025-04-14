from asyncio import gather

import psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from integration import markers
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


@markers.juju3
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
            channel="latest/edge",
            config={"database-name": TESTING_DATABASE},
        ),
        ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=SECOND_DATA_INTEGRATOR_APP_NAME,
            num_units=1,
            channel="latest/edge",
            config={"database-name": TESTING_DATABASE},
        ),
        ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=THIRD_DATA_INTEGRATOR_APP_NAME,
            num_units=1,
            channel="latest/edge",
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
        await ops_test.model.wait_for_idle(status="active", timeout=500)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_create_testing_data(ops_test: OpsTest):
    await _create_test_table(ops_test, DATA_INTEGRATOR_APP_NAME)
    await _create_test_table(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME)
    await _create_test_table(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME)
    await _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "first")


@markers.juju3
@pytest.mark.abort_on_fail
async def test_setup_logical_replication(ops_test: OpsTest):
    publisher_leader = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    first_subscriber_leader = await get_leader_unit(ops_test, SECOND_DATABASE_APP_NAME)
    second_subscriber_leader = await get_leader_unit(ops_test, THIRD_DATABASE_APP_NAME)

    # Logical replication between first and second database applications is already established in test_deploy
    await ops_test.model.integrate(
        f"{DATABASE_APP_NAME}:logical-replication-offer",
        f"{THIRD_DATABASE_APP_NAME}:logical-replication",
    )

    action = await publisher_leader.run_action(
        "add-publication",
        name="test_publication",
        database=TESTING_DATABASE,
        tables="public.test_table",
    )

    await action.wait()
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)

    action = await publisher_leader.run_action("list-publications")
    await action.wait()
    results = action.results.get("publications")
    assert results and "test_publication | 0" in results, (
        "publication should be listed in list-publications action"
    )

    action = await first_subscriber_leader.run_action("subscribe", name="test_publication")
    await action.wait()

    action = await first_subscriber_leader.run_action("list-subscriptions")
    await action.wait()
    results = action.results.get("subscriptions")
    assert results and "test_publication" in results, (
        "subscription should be listed in list-subscriptions action"
    )

    action = await second_subscriber_leader.run_action("subscribe", name="test_publication")
    await action.wait()

    action = await second_subscriber_leader.run_action("list-subscriptions")
    await action.wait()
    results = action.results.get("subscriptions")
    assert results and "test_publication" in results, (
        "subscription should be listed in list-subscriptions action"
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)

    action = await publisher_leader.run_action("list-publications")
    await action.wait()
    results = action.results.get("publications")
    assert results and "test_publication | 2" in results, (
        "publication should detect 2 active connections"
    )

    assert await _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "first"), (
        "testing table should be copied to the postgresql2 on logical replication setup"
    )
    assert await _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "first"), (
        "testing table should be copied to the postgresql3 on logical replication setup"
    )


@markers.juju3
@pytest.mark.abort_on_fail
async def test_logical_replication(ops_test: OpsTest):
    await _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "second")
    assert await _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "second"), (
        "logical replication should work with postgresql -> postgresql2"
    )
    assert await _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "second"), (
        "logical replication should work with postgresql -> postgresql3"
    )


@markers.juju3
@pytest.mark.abort_on_fail
async def test_switchover(ops_test: OpsTest):
    publisher_leader = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    publisher_roles = await get_cluster_roles(ops_test, publisher_leader.name)
    publisher_candidate = ops_test.model.units[publisher_roles["sync_standbys"][0]]
    action = await publisher_candidate.run_action("promote-to-primary", scope="unit", force=True)
    await action.wait()

    first_subscriber_leader = await get_leader_unit(ops_test, SECOND_DATABASE_APP_NAME)
    subscriber_roles = await get_cluster_roles(ops_test, first_subscriber_leader.name)
    subscriber_candidate = ops_test.model.units[subscriber_roles["sync_standbys"][0]]
    action = await subscriber_candidate.run_action("promote-to-primary", scope="unit", force=True)
    await action.wait()

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_logical_replication_after_switchover(ops_test: OpsTest):
    await _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "third")
    assert await _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "third"), (
        "logical replication should work with postgresql -> postgresql2"
    )
    assert await _check_test_data(ops_test, THIRD_DATA_INTEGRATOR_APP_NAME, "third"), (
        "logical replication should work with postgresql -> postgresql3"
    )


@markers.juju3
@pytest.mark.abort_on_fail
async def test_subscriber_removal(ops_test: OpsTest):
    await ops_test.model.remove_application(THIRD_DATA_INTEGRATOR_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(THIRD_DATABASE_APP_NAME, block_until_done=True)
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


@markers.juju3
@pytest.mark.abort_on_fail
async def test_logical_replication_after_subscriber_removal(ops_test: OpsTest):
    await _insert_test_data(ops_test, DATA_INTEGRATOR_APP_NAME, "fourth")
    assert await _check_test_data(ops_test, SECOND_DATA_INTEGRATOR_APP_NAME, "fourth"), (
        "logical replication should work with postgresql -> postgresql2"
    )


@markers.juju3
@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:logical-replication-offer",
        f"{SECOND_DATABASE_APP_NAME}:logical-replication",
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)
    connection_string = await build_connection_string(
        ops_test,
        SECOND_DATA_INTEGRATOR_APP_NAME,
        DATA_INTEGRATOR_RELATION,
        database=TESTING_DATABASE,
    )
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(1) FROM pg_subscription;")
        assert cursor.fetchone()[0] == 0, (
            "unused PostgreSQL subscription should be removed in the subscriber cluster"
        )


async def _create_test_table(ops_test: OpsTest, data_integrator_app_name: str) -> None:
    with (
        psycopg2.connect(
            await build_connection_string(
                ops_test,
                data_integrator_app_name,
                DATA_INTEGRATOR_RELATION,
                database=TESTING_DATABASE,
            )
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("CREATE TABLE test_table (test_column text);")


async def _insert_test_data(ops_test: OpsTest, data_integrator_app_name: str, data: str) -> None:
    with (
        psycopg2.connect(
            await build_connection_string(
                ops_test,
                data_integrator_app_name,
                DATA_INTEGRATOR_RELATION,
                database=TESTING_DATABASE,
            )
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO test_table (test_column) VALUES (%s);",
            (data,),
        )


async def _check_test_data(ops_test: OpsTest, data_integrator_app_name: str, data: str) -> bool:
    with (
        psycopg2.connect(
            await build_connection_string(
                ops_test,
                data_integrator_app_name,
                DATA_INTEGRATOR_RELATION,
                database=TESTING_DATABASE,
            )
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM test_table WHERE test_column = %s);",
            (data,),
        )
        return cursor.fetchone()[0]
