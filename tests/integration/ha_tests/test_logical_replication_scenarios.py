# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the logical replication empty-table guard.

Covers the scenarios validated for canonical/postgresql-k8s-operator#982:
- the basic one-way replication flow from the PR body (anchor issue-3120052507);
- the re-relation duplication bug from comment 3019811325 (removing and
  re-integrating the relation must NOT duplicate the subscriber rows);
- the config-cycle variant of the same bug and the TRUNCATE recovery path.

These tests complement test_logical_replication_circular.py, which covers the
cyclic (bidirectional) setups of canonical/postgresql-k8s-operator#1052.
"""

import json
from asyncio import gather

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from integration.helpers import CHARM_BASE
from integration.new_relations.helpers import build_connection_string

APP_NAME_A = "postgresql-sc1"
APP_NAME_B = "postgresql-sc2"

DATA_INTEGRATOR_A = "data-integrator-sc1"
DATA_INTEGRATOR_B = "data-integrator-sc2"
DATA_INTEGRATOR_RELATION = "postgresql"

APP_CONFIG = {"profile": "testing"}
TESTING_DATABASE = "testdb"
REQUEST_CONFIG = {TESTING_DATABASE: ["public.asd"]}


async def _run_query(ops_test: OpsTest, data_integrator: str, query: str) -> list[tuple]:
    """Run a query against the data integrator database and return all rows."""
    connection = None
    try:
        # The data-integrator relation can take several minutes to complete on a
        # cold environment; retry the credential read together with the connect.
        for attempt in Retrying(stop=stop_after_delay(600), wait=wait_fixed(15), reraise=True):
            with attempt:
                connection_string = await build_connection_string(
                    ops_test,
                    data_integrator,
                    DATA_INTEGRATOR_RELATION,
                    database=TESTING_DATABASE,
                )
                connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(query)
            if cursor.description is None:
                return []
            return cursor.fetchall()
    finally:
        if connection is not None:
            connection.close()


async def _subscription_count(ops_test: OpsTest) -> int:
    """Number of logical replication subscriptions in the subscriber database."""
    rows = await _run_query(ops_test, DATA_INTEGRATOR_B, "SELECT count(*) FROM pg_subscription;")
    return rows[0][0]


async def _wait_for_row_count(ops_test: OpsTest, expected: int, timeout: int = 120) -> int:
    """Wait until the subscriber table reaches the expected row count."""
    for attempt in Retrying(stop=stop_after_delay(timeout), wait=wait_fixed(5), reraise=True):
        with attempt:
            rows = await _run_query(ops_test, DATA_INTEGRATOR_B, "SELECT count(*) FROM asd;")
            assert rows[0][0] == expected, f"subscriber rows: {rows[0][0]} != {expected}"
    return rows[0][0]


@pytest.mark.abort_on_fail
async def test_deploy_clusters(ops_test: OpsTest, charm):
    """Deploy two single-unit PostgreSQL clusters with their data integrators."""
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
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME_A, APP_NAME_B],
        status="active",
        timeout=2500,
        raise_on_error=False,
    )
    async with ops_test.fast_forward():
        await gather(
            ops_test.model.integrate(APP_NAME_A, DATA_INTEGRATOR_A),
            ops_test.model.integrate(APP_NAME_B, DATA_INTEGRATOR_B),
        )
        await ops_test.model.wait_for_idle(status="active", timeout=500)


@pytest.mark.abort_on_fail
async def test_basic_one_way_replication(ops_test: OpsTest):
    """The PR body scenario: initial copy on subscribe and ongoing push."""
    # Publisher table with data, subscriber table empty.
    await _run_query(ops_test, DATA_INTEGRATOR_A, "CREATE TABLE asd (message text);")
    await _run_query(ops_test, DATA_INTEGRATOR_A, "INSERT INTO asd VALUES ('hello');")
    await _run_query(ops_test, DATA_INTEGRATOR_B, "CREATE TABLE asd (message text);")

    # Establish the one-way relation, then configure the subscriber.
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_A}:logical-replication-offer",
            f"{APP_NAME_B}:logical-replication",
        )
        await ops_test.model.wait_for_idle(status="active", timeout=500)

    config_b = APP_CONFIG.copy()
    config_b["logical-replication-subscription-request"] = json.dumps(REQUEST_CONFIG)
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)

    # Initial copy.
    assert await _wait_for_row_count(ops_test, 1) == 1

    # Ongoing push.
    await _run_query(ops_test, DATA_INTEGRATOR_A, "INSERT INTO asd VALUES ('a2'), ('a3');")
    assert await _wait_for_row_count(ops_test, 3) == 3


@pytest.mark.abort_on_fail
async def test_rerelation_no_duplication(ops_test: OpsTest):
    """Re-integrating the relation must not duplicate the subscriber rows.

    Upstream bug: canonical/postgresql-k8s-operator#982 comment 3019811325
    (6 rows became 12 after remove + re-integrate).
    """
    # Grow the replicated table to six rows.
    await _run_query(ops_test, DATA_INTEGRATOR_A, "INSERT INTO asd VALUES ('d4'), ('d5'), ('d6');")
    assert await _wait_for_row_count(ops_test, 6) == 6
    before = dict(
        await _run_query(ops_test, DATA_INTEGRATOR_B, "SELECT message, md5(message) FROM asd;")
    )
    assert len(before) == 6

    # Remove the relation: the subscription is dropped, the data stays.
    async with ops_test.fast_forward():
        await ops_test.juju(
            "remove-relation",
            f"{APP_NAME_A}:logical-replication-offer",
            f"{APP_NAME_B}:logical-replication",
        )
    assert await _subscription_count(ops_test) == 0
    rows_after_break = dict(
        await _run_query(ops_test, DATA_INTEGRATOR_B, "SELECT message, md5(message) FROM asd;")
    )
    assert rows_after_break == before

    # Re-integrate with NO config change: the guard must block the subscribe
    # because the subscriber table is not empty.
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{APP_NAME_A}:logical-replication-offer",
            f"{APP_NAME_B}:logical-replication",
        )
        await ops_test.model.wait_for_idle(status="active", timeout=500)

    assert await _subscription_count(ops_test) == 0
    rows = dict(
        await _run_query(ops_test, DATA_INTEGRATOR_B, "SELECT message, md5(message) FROM asd;")
    )
    assert rows == before, "subscriber rows changed on re-integration"


@pytest.mark.abort_on_fail
async def test_config_cycle_no_duplication(ops_test: OpsTest):
    """Cycling the subscription config must not duplicate a non-empty table."""
    # Cycle the config: unset, then set the same request again.
    config_b = APP_CONFIG.copy()
    config_b["logical-replication-subscription-request"] = "{}"
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)
    assert await _subscription_count(ops_test) == 0

    config_b["logical-replication-subscription-request"] = json.dumps(REQUEST_CONFIG)
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)

    # The guard blocks the subscribe: no subscription, rows unchanged.
    assert await _subscription_count(ops_test) == 0
    rows = dict(
        await _run_query(ops_test, DATA_INTEGRATOR_B, "SELECT message, md5(message) FROM asd;")
    )
    assert len(rows) == 6


@pytest.mark.abort_on_fail
async def test_truncate_resubscribe_recovery(ops_test: OpsTest):
    """After truncating the subscriber table, a clean re-subscribe works."""
    await _run_query(ops_test, DATA_INTEGRATOR_B, "TRUNCATE asd;")

    config_b = APP_CONFIG.copy()
    config_b["logical-replication-subscription-request"] = "{}"
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=500)

    config_b["logical-replication-subscription-request"] = json.dumps(REQUEST_CONFIG)
    await ops_test.model.applications[APP_NAME_B].set_config(config_b)

    # Single copy of the publisher rows, no duplication.
    for attempt in Retrying(stop=stop_after_delay(180), wait=wait_fixed(5), reraise=True):
        with attempt:
            rows = await _run_query(
                ops_test, DATA_INTEGRATOR_B, "SELECT count(*), count(DISTINCT message) FROM asd;"
            )
            assert rows[0] == (6, 6), f"subscriber rows: {rows[0]}"

    # The re-created subscription is live.
    await _run_query(ops_test, DATA_INTEGRATOR_A, "INSERT INTO asd VALUES ('e7');")
    assert await _wait_for_row_count(ops_test, 7) == 7
