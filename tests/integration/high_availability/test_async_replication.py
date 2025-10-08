#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju

from .. import architecture
from ..markers import juju3
from .high_availability_helpers_new import (
    get_app_leader,
    get_app_units,
    get_postgresql_cluster_status,
    get_postgresql_max_written_value,
    wait_for_apps_status,
)

POSTGRESQL_APP_1 = "db1"
POSTGRESQL_APP_2 = "db2"
POSTGRESQL_TEST_APP_NAME = "postgresql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def first_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Creates and return the first model."""
    yield juju.model


@pytest.fixture(scope="module")
def second_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Creates and returns the second model."""
    model_name = f"{juju.model}-other"

    logging.info(f"Creating model: {model_name}")
    juju.add_model(model_name)

    yield model_name
    if request.config.getoption("--keep-models"):
        return

    logging.info(f"Destroying model: {model_name}")
    juju.destroy_model(model_name, destroy_storage=True, force=True)


@pytest.fixture()
def continuous_writes(first_model: str) -> Generator:
    """Starts continuous writes to the MySQL cluster for a test and clear the writes at the end."""
    model_1 = Juju(model=first_model)
    model_1_test_app_leader = get_app_leader(model_1, POSTGRESQL_TEST_APP_NAME)

    logging.info("Clearing continuous writes")
    model_1.run(model_1_test_app_leader, "clear-continuous-writes")
    logging.info("Starting continuous writes")
    model_1.run(model_1_test_app_leader, "start-continuous-writes")

    yield

    logging.info("Clearing continuous writes")
    model_1.run(model_1_test_app_leader, "clear-continuous-writes")


@juju3
@pytest.mark.abort_on_fail
def test_build_and_deploy(first_model: str, second_model: str, charm: str) -> None:
    """Simple test to ensure that the MySQL application charms get deployed."""
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}

    logging.info("Deploying postgresql clusters")
    model_1 = Juju(model=first_model)
    model_1.deploy(
        charm=charm,
        app=POSTGRESQL_APP_1,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=3,
    )
    model_2 = Juju(model=second_model)
    model_2.deploy(
        charm=charm,
        app=POSTGRESQL_APP_2,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=3,
    )

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_1),
        timeout=15 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_2),
        timeout=15 * MINUTE_SECS,
    )


@juju3
@pytest.mark.abort_on_fail
def test_async_relate(first_model: str, second_model: str) -> None:
    """Relate the two MySQL clusters."""
    logging.info("Creating offers in first model")
    model_1 = Juju(model=first_model)
    model_1.offer(POSTGRESQL_APP_1, endpoint="replication-offer")

    logging.info("Consuming offer in second model")
    model_2 = Juju(model=second_model)
    model_2.consume(f"{first_model}.{POSTGRESQL_APP_1}")

    logging.info("Relating the two postgresql clusters")
    model_2.integrate(
        f"{POSTGRESQL_APP_1}",
        f"{POSTGRESQL_APP_2}:replication",
    )

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.any_blocked, POSTGRESQL_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.any_waiting, POSTGRESQL_APP_2),
        timeout=10 * MINUTE_SECS,
    )


@juju3
@pytest.mark.abort_on_fail
def test_deploy_app(first_model: str) -> None:
    """Deploy the router and the test application."""
    logging.info("Deploying test application")
    model_1 = Juju(model=first_model)
    model_1.deploy(
        charm=POSTGRESQL_TEST_APP_NAME,
        app=POSTGRESQL_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        num_units=1,
        trust=False,
    )

    logging.info("Relating test application")
    model_1.integrate(
        f"{POSTGRESQL_TEST_APP_NAME}:database",
        f"{POSTGRESQL_APP_1}:database",
    )

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_TEST_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )


@juju3
@pytest.mark.abort_on_fail
def test_create_replication(first_model: str, second_model: str) -> None:
    """Run the create-replication action and wait for the applications to settle."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Running create replication action")
    task = model_1.run(
        unit=get_app_leader(model_1, POSTGRESQL_APP_1),
        action="create-replication",
        wait=5 * MINUTE_SECS,
    )
    task.raise_on_failure()

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_2),
        timeout=10 * MINUTE_SECS,
    )


@juju3
@pytest.mark.abort_on_fail
async def test_data_replication(first_model: str, second_model: str, continuous_writes) -> None:
    """Test to write to primary, and read the same data back from replicas."""
    logging.info("Testing data replication")
    results = await get_postgresql_max_written_values(first_model, second_model)

    assert len(results) == 6
    assert all(results[0] == x for x in results), "Data is not consistent across units"
    assert results[0] > 1, "No data was written to the database"


@juju3
@pytest.mark.abort_on_fail
async def test_standby_promotion(first_model: str, second_model: str, continuous_writes) -> None:
    """Test graceful promotion of a standby cluster to primary."""
    model_2 = Juju(model=second_model)
    model_2_postgresql_leader = get_app_leader(model_2, POSTGRESQL_APP_2)

    logging.info("Promoting standby cluster to primary")
    promotion_task = model_2.run(
        unit=model_2_postgresql_leader,
        action="promote-to-primary",
        params={"scope": "cluster", "force": "true"},
    )
    promotion_task.raise_on_failure()

    results = await get_postgresql_max_written_values(first_model, second_model)
    assert len(results) == 6
    assert all(results[0] == x for x in results), "Data is not consistent across units"
    assert results[0] > 1, "No data was written to the database"

    cluster_set_status = get_postgresql_cluster_status(
        juju=model_2,
        unit=model_2_postgresql_leader,
        cluster_set=True,
    )

    assert cluster_set_status["clusters"]["cuzco"]["clusterrole"] == "primary", (
        "standby not promoted to primary"
    )


@juju3
@pytest.mark.abort_on_fail
def test_failover(first_model: str, second_model: str) -> None:
    """Test switchover on primary cluster fail."""
    logging.info("Freezing postgres on primary cluster units")
    model_2 = Juju(model=second_model)
    model_2_postgresql_units = get_app_units(model_2, POSTGRESQL_APP_2)

    # Simulating a failure on the primary cluster
    for unit_name in model_2_postgresql_units:
        model_2.exec("sudo pkill -x postgres --signal SIGSTOP", unit=unit_name)

    logging.info("Promoting standby cluster to primary with force flag")
    model_1 = Juju(model=first_model)
    model_1_postgresql_leader = get_app_leader(model_1, POSTGRESQL_APP_1)

    promotion_task = model_1.run(
        unit=model_1_postgresql_leader,
        action="promote-to-primary",
        params={"scope": "cluster", "force": True},
        wait=5 * MINUTE_SECS,
    )
    promotion_task.raise_on_failure()

    # Restore postgres process
    logging.info("Unfreezing postgres on primary cluster units")
    for unit_name in model_2_postgresql_units:
        model_2.exec("sudo pkill -x postgres --signal SIGCONT", unit=unit_name)

    logging.info("Checking clusters statuses")
    cluster_set_status = get_postgresql_cluster_status(
        juju=model_1,
        unit=model_1_postgresql_leader,
        cluster_set=True,
    )

    assert cluster_set_status["clusters"]["lima"]["clusterrole"] == "primary", (
        "standby not promoted to primary",
    )
    assert cluster_set_status["clusters"]["cuzco"]["globalstatus"] == "invalidated", (
        "old primary not invalidated"
    )


@juju3
@pytest.mark.abort_on_fail
async def test_rejoin_invalidated_cluster(
    first_model: str, second_model: str, continuous_writes
) -> None:
    """Test rejoin invalidated cluster with."""
    model_1 = Juju(model=first_model)
    model_1_postgresql_leader = get_app_leader(model_1, POSTGRESQL_APP_1)

    task = model_1.run(
        unit=model_1_postgresql_leader,
        action="rejoin-cluster",
        wait=5 * MINUTE_SECS,
    )
    task.raise_on_failure()

    results = await get_postgresql_max_written_values(first_model, second_model)
    assert len(results) == 6
    assert all(results[0] == x for x in results), "Data is not consistent across units"
    assert results[0] > 1, "No data was written to the database"


@juju3
@pytest.mark.abort_on_fail
async def test_unrelate_and_relate(first_model: str, second_model: str, continuous_writes) -> None:
    """Test removing and re-relating the two postgresql clusters."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Remove async relation")
    model_2.remove_relation(
        f"{POSTGRESQL_APP_1}",
        f"{POSTGRESQL_APP_2}:replication",
    )

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_blocked, POSTGRESQL_APP_2),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Re relating the two postgresql clusters")
    model_2.integrate(
        f"{POSTGRESQL_APP_1}",
        f"{POSTGRESQL_APP_2}:replication",
    )
    model_1.wait(
        ready=wait_for_apps_status(jubilant.any_blocked, POSTGRESQL_APP_1),
        timeout=5 * MINUTE_SECS,
    )

    logging.info("Running create replication action")
    task = model_1.run(
        unit=get_app_leader(model_1, POSTGRESQL_APP_1),
        action="create-replication",
        wait=5 * MINUTE_SECS,
    )
    task.raise_on_failure()

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, POSTGRESQL_APP_2),
        timeout=10 * MINUTE_SECS,
    )

    results = await get_postgresql_max_written_values(first_model, second_model)
    assert len(results) == 6
    assert all(results[0] == x for x in results), "Data is not consistent across units"
    assert results[0] > 1, "No data was written to the database"


async def get_postgresql_max_written_values(first_model: str, second_model: str) -> list[int]:
    """Return list with max written value from all units."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Stopping continuous writes")
    stopping_task = model_1.run(
        unit=get_app_leader(model_1, POSTGRESQL_TEST_APP_NAME), action="stop-continuous-writes"
    )
    stopping_task.raise_on_failure()

    time.sleep(5)
    results = []

    logging.info(f"Querying max value on all {POSTGRESQL_APP_1} units")
    for unit_name in get_app_units(model_1, POSTGRESQL_APP_1):
        unit_max_value = await get_postgresql_max_written_value(
            model_1, POSTGRESQL_APP_1, unit_name
        )
        results.append(unit_max_value)

    logging.info(f"Querying max value on all {POSTGRESQL_APP_2} units")
    for unit_name in get_app_units(model_2, POSTGRESQL_APP_2):
        unit_max_value = await get_postgresql_max_written_value(
            model_2, POSTGRESQL_APP_2, unit_name
        )
        results.append(unit_max_value)

    return results
