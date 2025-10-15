#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju
from tenacity import Retrying, stop_after_attempt

from .. import architecture
from .high_availability_helpers_new import (
    get_app_leader,
    get_app_units,
    get_db_max_written_value,
    wait_for_apps_status,
)

DB_APP_NAME = "postgresql"
DB_APP_1 = "db1"
DB_APP_2 = "db2"
DB_TEST_APP_NAME = "postgresql-test-app"

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
def first_model_continuous_writes(first_model: str) -> Generator:
    """Starts continuous writes to the cluster for a test and clear the writes at the end."""
    model_1 = Juju(model=first_model)
    application_unit = get_app_leader(model_1, DB_TEST_APP_NAME)

    logging.info("Clearing continuous writes")
    model_1.run(
        unit=application_unit, action="clear-continuous-writes", wait=120
    ).raise_on_failure()

    logging.info("Starting continuous writes")

    for attempt in Retrying(stop=stop_after_attempt(10), reraise=True):
        with attempt:
            result = model_1.run(unit=application_unit, action="start-continuous-writes")
            result.raise_on_failure()

            assert result.results["result"] == "True"

    yield

    logging.info("Clearing continuous writes")
    model_1.run(
        unit=application_unit, action="clear-continuous-writes", wait=120
    ).raise_on_failure()


def test_deploy(first_model: str, second_model: str, charm: str) -> None:
    """Simple test to ensure that the MySQL application charms get deployed."""
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}

    # TODO Deploy from edge
    logging.info("Deploying postgresql clusters")
    model_1 = Juju(model=first_model)
    model_1.deploy(
        charm=charm,
        app=DB_APP_1,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=3,
    )
    model_2 = Juju(model=second_model)
    model_2.deploy(
        charm=charm,
        app=DB_APP_2,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=3,
    )

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
    )


def test_async_relate(first_model: str, second_model: str) -> None:
    """Relate the two MySQL clusters."""
    logging.info("Creating offers in first model")
    model_1 = Juju(model=first_model)
    model_1.offer(f"{first_model}.{DB_APP_1}", endpoint="replication-offer")

    logging.info("Consuming offer in second model")
    model_2 = Juju(model=second_model)
    model_2.consume(f"{first_model}.{DB_APP_1}")

    logging.info("Relating the two postgresql clusters")
    model_2.integrate(f"{DB_APP_1}", f"{DB_APP_2}:replication")

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_2),
        timeout=10 * MINUTE_SECS,
    )


def test_deploy_test_app(first_model: str) -> None:
    """Deploy the test application."""
    constraints = {"arch": architecture.architecture}

    logging.info("Deploying the test application")
    model_1 = Juju(model=first_model)
    model_1.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        constraints=constraints,
        num_units=1,
    )

    logging.info("Relating the test application")
    model_1.integrate(f"{DB_APP_1}:database", f"{DB_TEST_APP_NAME}:database")

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_TEST_APP_NAME), timeout=10 * MINUTE_SECS
    )


def test_create_replication(first_model: str, second_model: str) -> None:
    """Run the create-replication action and wait for the applications to settle."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Running create replication action")
    model_1.run(
        unit=get_app_leader(model_1, DB_APP_1), action="create-replication", wait=5 * MINUTE_SECS
    ).raise_on_failure()

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
    )


def test_upgrade_from_edge(
    first_model: str, second_model: str, charm: str, first_model_continuous_writes
) -> None:
    """Upgrade the two MySQL clusters."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    run_pre_refresh_checks(model_1, DB_APP_1)
    run_upgrade_from_edge(model_1, DB_APP_1, charm)

    run_pre_refresh_checks(model_2, DB_APP_2)
    run_upgrade_from_edge(model_2, DB_APP_2, charm)


def test_data_replication(
    first_model: str, second_model: str, first_model_continuous_writes
) -> None:
    """Test to write to primary, and read the same data back from replicas."""
    logging.info("Testing data replication")
    results = get_db_max_written_values(first_model, second_model)

    assert len(results) == 6
    assert all(results[0] == x for x in results), "Data is not consistent across units"
    assert results[0] > 1, "No data was written to the database"


def get_db_max_written_values(first_model: str, second_model: str) -> list[int]:
    """Return list with max written value from all units."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Stopping continuous writes")
    stopping_task = model_1.run(
        unit=get_app_leader(model_1, DB_TEST_APP_NAME), action="stop-continuous-writes", params={}
    )
    stopping_task.raise_on_failure()

    time.sleep(5)
    results = []

    logging.info(f"Querying max value on all {DB_APP_1} units")
    for unit_name in get_app_units(model_1, DB_APP_1):
        unit_max_value = get_db_max_written_value(model_1, DB_APP_1, unit_name)
        results.append(unit_max_value)

    logging.info(f"Querying max value on all {DB_APP_2} units")
    for unit_name in get_app_units(model_2, DB_APP_2):
        unit_max_value = get_db_max_written_value(model_2, DB_APP_2, unit_name)
        results.append(unit_max_value)

    return results


def run_pre_refresh_checks(juju: Juju, app_name: str) -> None:
    """Run the pre-refresh-check actions."""
    app_leader = get_app_leader(juju, app_name)

    logging.info("Run pre-upgrade-check action")
    juju.run(unit=app_leader, action="pre-refresh-check").raise_on_failure()


def run_upgrade_from_edge(juju: Juju, app_name: str, charm: str) -> None:
    """Update the second cluster."""
    logging.info("Refresh the charm")
    juju.refresh(app=app_name, path=charm)
    logging.info("Wait for refresh to block as paused or incompatible")
    try:
        juju.wait(lambda status: status.apps[app_name].is_blocked, timeout=5 * MINUTE_SECS)

        units = get_app_units(juju, app_name)
        unit_names = sorted(units.keys())

        if "Refresh incompatible" in juju.status().apps[app_name].app_status.message:
            logging.info("Application refresh is blocked due to incompatibility")
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=5 * MINUTE_SECS,
            )

            juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

        logging.info("Run resume-refresh action")
        juju.run(unit=unit_names[1], action="resume-refresh", wait=5 * MINUTE_SECS)
    except TimeoutError:
        logging.info("Upgrade completed without snap refresh (charm.py upgrade only)")
        assert juju.status().apps[app_name].is_active

    logging.info("Wait for upgrade to complete")
    juju.wait(ready=wait_for_apps_status(jubilant.all_active, app_name), timeout=20 * MINUTE_SECS)
