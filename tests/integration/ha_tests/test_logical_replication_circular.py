# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for logical replication circular detection.

Adapted from the K8s charm's test_logical_replication_circular.py: verifies that
circular replication setups are blocked instead of looping inserts forever.
"""

import json
from asyncio import gather

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from integration.helpers import CHARM_BASE, get_leader_unit
from integration.new_relations.helpers import build_connection_string

APP_NAME_A = "postgresql-a"
APP_NAME_B = "postgresql-b"
APP_NAME_C = "postgresql-c"

DATA_INTEGRATOR_A = "data-integrator-a"
DATA_INTEGRATOR_B = "data-integrator-b"
DATA_INTEGRATOR_C = "data-integrator-c"
DATA_INTEGRATOR_RELATION = "postgresql"

APP_CONFIG = {"profile": "testing"}
TESTING_DATABASE = "testdb"


@pytest.mark.abort_on_fail
async def test_deploy_clusters(ops_test: OpsTest, charm):
    """Deploy three PostgreSQL clusters for testing circular replication."""
    await gather(
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME_A,
            num_units=1,
            base=CHARM_BASE,
            config=APP_CONFIG,
        ),
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME_B,
            num_units=1,
            base=CHARM_BASE,
            config=APP_CONFIG,
        ),
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME_C,
            num_units=1,
            base=CHARM_BASE,
            config=APP_CONFIG,
        ),
        ops_test.model.deploy(
            "data-integrator",
            application_name=DATA_INTEGRATOR_A,
            num_units=1,
            channel="latest/stable",
            config={"database-name": TESTING_DATABASE},
        ),
        ops_test.model.deploy(
            "data-integrator",
            application_name=DATA_INTEGRATOR_B,
            num_units=1,
            channel="latest/stable",
            config={"database-name": TESTING_DATABASE},
        ),
        ops_test.model.deploy(
            "data-integrator",
            application_name=DATA_INTEGRATOR_C,
            num_units=1,
            channel="latest/stable",
            config={"database-name": TESTING_DATABASE},
        ),
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME_A, APP_NAME_B, APP_NAME_C],
        status="active",
        timeout=2500,
        raise_on_error=False,
    )

    # Integrate data integrators with their clusters
    async with ops_test.fast_forward():
        await gather(
            ops_test.model.integrate(APP_NAME_A, DATA_INTEGRATOR_A),
            ops_test.model.integrate(APP_NAME_B, DATA_INTEGRATOR_B),
            ops_test.model.integrate(APP_NAME_C, DATA_INTEGRATOR_C),
        )
        await ops_test.model.wait_for_idle(status="active", timeout=500)


@pytest.mark.abort_on_fail
async def test_setup_tables(ops_test: OpsTest):
    """Create test tables in all clusters."""
    await gather(
        _create_test_table(ops_test, DATA_INTEGRATOR_A, "users"),
        _create_test_table(ops_test, DATA_INTEGRATOR_A, "orders"),
        _create_test_table(ops_test, DATA_INTEGRATOR_B, "users"),
        _create_test_table(ops_test, DATA_INTEGRATOR_B, "orders"),
        _create_test_table(ops_test, DATA_INTEGRATOR_C, "users"),
        _create_test_table(ops_test, DATA_INTEGRATOR_C, "orders"),
    )


@pytest.mark.abort_on_fail
async def test_bidirectional_same_table_blocked(ops_test: OpsTest):
    """Test that bidirectional replication of the same table is blocked.

    Scenario: Cluster A <-> Cluster B (both trying to replicate public.users)
    Expected: Blocked with circular replication error
    """
    # First, setup the logical replication relation A -> B
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_A}:logical-replication-offer",
            f"{APP_NAME_B}:logical-replication",
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B],
            status="active",
            timeout=500,
        )

    # Configure B to subscribe to users from A
    config_b = APP_CONFIG.copy()
    config_b["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.users"]
    })
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B],
            status="active",
            timeout=500,
        )

    # Verify B is subscribed and replication works
    await _insert_test_data(ops_test, DATA_INTEGRATOR_A, "from_a", "users")
    await _check_test_data(ops_test, DATA_INTEGRATOR_B, "from_a", "users")

    # Now setup B -> A replication (reverse direction)
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_B}:logical-replication-offer",
            f"{APP_NAME_A}:logical-replication",
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B],
            timeout=500,
        )

    # Try to configure A to subscribe to the SAME table from B
    # This should be blocked due to circular replication
    config_a = APP_CONFIG.copy()
    config_a["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.users"]
    })
    await ops_test.model.applications[APP_NAME_A].set_config(config_a)

    # Wait for the leader unit to go to blocked status
    leader_unit_a = await get_leader_unit(ops_test, APP_NAME_A)
    await ops_test.model.block_until(
        lambda: leader_unit_a.workload_status == "blocked", timeout=120
    )

    # Verify the status message mentions circular replication
    assert "circular replication" in leader_unit_a.workload_status_message.lower()


@pytest.mark.abort_on_fail
async def test_bidirectional_different_tables_allowed(ops_test: OpsTest):
    """Test that bidirectional replication of different tables is allowed.

    Scenario: Cluster A -> B (public.users), Cluster B -> A (public.orders)
    Expected: Both work fine, no circular replication
    """
    # A is already subscribed to users from B (but blocked)
    # Let's change to orders instead
    config_a = APP_CONFIG.copy()
    config_a["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.orders"]
    })
    await ops_test.model.applications[APP_NAME_A].set_config(config_a)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B], status="active", timeout=500
        )

    # Verify bidirectional replication works for different tables
    await _insert_test_data(ops_test, DATA_INTEGRATOR_A, "a_users", "users")
    await _insert_test_data(ops_test, DATA_INTEGRATOR_B, "b_orders", "orders")

    # Check A -> B (users)
    await _check_test_data(ops_test, DATA_INTEGRATOR_B, "a_users", "users")

    # Check B -> A (orders)
    await _check_test_data(ops_test, DATA_INTEGRATOR_A, "b_orders", "orders")


@pytest.mark.abort_on_fail
async def test_cleanup_ab_relations(ops_test: OpsTest):
    """Clean up A-B relations before testing multi-hop scenario."""
    # Remove all logical replication relations between A and B
    await ops_test.model.applications[APP_NAME_A].remove_relation(
        f"{APP_NAME_A}:logical-replication-offer",
        f"{APP_NAME_B}:logical-replication",
    )
    await ops_test.model.applications[APP_NAME_B].remove_relation(
        f"{APP_NAME_B}:logical-replication-offer",
        f"{APP_NAME_A}:logical-replication",
    )

    # Clear subscription configs
    config_a = APP_CONFIG.copy()
    config_a["logical-replication-subscription-request"] = ""
    await ops_test.model.applications[APP_NAME_A].set_config(config_a)

    config_b = APP_CONFIG.copy()
    config_b["logical-replication-subscription-request"] = ""
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B], status="active", timeout=500
        )

    # Truncate tables to start fresh
    await _truncate_table(ops_test, DATA_INTEGRATOR_A, "users")
    await _truncate_table(ops_test, DATA_INTEGRATOR_B, "users")


@pytest.mark.abort_on_fail
async def test_multihop_circular_blocked(ops_test: OpsTest):
    """Test that multi-hop circular replication is blocked.

    Scenario: Cluster A -> B -> C -> A (all for public.users)
    Expected: The final A subscription is blocked with circular replication error
    """
    # Setup A -> B replication
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_A}:logical-replication-offer",
            f"{APP_NAME_B}:logical-replication",
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B],
            status="active",
            timeout=500,
        )

    config_b = APP_CONFIG.copy()
    config_b["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.users"]
    })
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_B],
            status="active",
            timeout=500,
        )

    # Verify A -> B works
    await _insert_test_data(ops_test, DATA_INTEGRATOR_A, "from_a", "users")
    await _check_test_data(ops_test, DATA_INTEGRATOR_B, "from_a", "users")

    # Setup B -> C replication
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_B}:logical-replication-offer",
            f"{APP_NAME_C}:logical-replication",
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_B, APP_NAME_C],
            status="active",
            timeout=500,
        )

    config_c = APP_CONFIG.copy()
    config_c["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.users"]
    })
    await ops_test.model.applications[APP_NAME_C].set_config(config_c)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_B, APP_NAME_C],
            status="active",
            timeout=500,
        )

    # Verify A -> B -> C works
    await _insert_test_data(ops_test, DATA_INTEGRATOR_A, "from_a_via_b", "users")
    await _check_test_data(ops_test, DATA_INTEGRATOR_C, "from_a_via_b", "users")

    # Now try to setup C -> A replication (completing the circle)
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_C}:logical-replication-offer",
            f"{APP_NAME_A}:logical-replication",
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_C],
            timeout=500,
        )

    # Try to configure A to subscribe to users from C
    # This should be blocked because the chain is: A -> B -> C -> (back to A)
    config_a = APP_CONFIG.copy()
    config_a["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.users"]
    })
    await ops_test.model.applications[APP_NAME_A].set_config(config_a)

    # Wait for the leader unit to go to blocked status
    leader_unit_a = await get_leader_unit(ops_test, APP_NAME_A)
    await ops_test.model.block_until(
        lambda: leader_unit_a.workload_status == "blocked", timeout=120
    )

    # Verify the status message mentions circular replication
    assert "circular replication" in leader_unit_a.workload_status_message.lower()


@pytest.mark.abort_on_fail
async def test_multihop_different_table_allowed(ops_test: OpsTest):
    """Test that multi-hop replication of different table works.

    Scenario: A -> B -> C (public.users), C -> A (public.orders)
    Expected: Works fine since orders doesn't create a cycle
    """
    # Change A's subscription to orders instead of users
    config_a = APP_CONFIG.copy()
    config_a["logical-replication-subscription-request"] = json.dumps({
        TESTING_DATABASE: ["public.orders"]
    })
    await ops_test.model.applications[APP_NAME_A].set_config(config_a)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_A, APP_NAME_C], status="active", timeout=500
        )

    # Verify the replication works
    await _insert_test_data(ops_test, DATA_INTEGRATOR_C, "c_orders", "orders")
    await _check_test_data(ops_test, DATA_INTEGRATOR_A, "c_orders", "orders")

    # Verify the A -> B -> C chain still works for users
    await _insert_test_data(ops_test, DATA_INTEGRATOR_A, "final_test", "users")
    await _check_test_data(ops_test, DATA_INTEGRATOR_C, "final_test", "users")


# Helper functions


async def _create_test_table(
    ops_test: OpsTest, data_integrator_app_name: str, table_name: str
) -> None:
    """Create a test table in the specified database."""
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


async def _truncate_table(
    ops_test: OpsTest, data_integrator_app_name: str, table_name: str
) -> None:
    """Truncate a table in the specified database."""
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
            cursor.execute(f"TRUNCATE TABLE {table_name};")
    finally:
        if connection is not None:
            connection.close()


async def _insert_test_data(
    ops_test: OpsTest, data_integrator_app_name: str, data: str, table_name: str
) -> None:
    """Insert test data into a table."""
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
    ops_test: OpsTest, data_integrator_app_name: str, data: str, table_name: str
) -> bool:
    """Check if test data exists in a table."""
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
