# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
import pytest
from jubilant import Juju

from .high_availability_helpers_new import (
    check_db_units_writes_increment,
    get_app_leader,
    get_app_units,
    wait_for_apps_status,
)

DB_APP_NAME = "postgresql"
DB_TEST_APP_NAME = "postgresql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.mark.abort_on_fail
def test_deploy_latest(juju: Juju) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    logging.info("Deploying PostgreSQL cluster")
    juju.deploy(
        charm=DB_APP_NAME,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        channel="16/stable",
        config={"profile": "testing"},
        num_units=3,
    )
    juju.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        num_units=1,
    )

    juju.integrate(
        f"{DB_APP_NAME}:database",
        f"{DB_TEST_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, DB_TEST_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


@pytest.mark.abort_on_fail
async def test_pre_refresh_check(juju: Juju) -> None:
    """Test that the pre-refresh-check action runs successfully."""
    db_leader = get_app_leader(juju, DB_APP_NAME)

    logging.info("Run pre-refresh-check action")
    task = juju.run(unit=db_leader, action="pre-refresh-check")
    task.raise_on_failure()


@pytest.mark.abort_on_fail
async def test_upgrade_from_stable(juju: Juju, charm: str, continuous_writes) -> None:
    """Update the second cluster."""
    logging.info("Ensure continuous writes are incrementing")
    await check_db_units_writes_increment(juju, DB_APP_NAME)

    logging.info("Refresh the charm")
    juju.refresh(app=DB_APP_NAME, path=charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant.any_maintenance(status, DB_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Application refresh is blocked due to incompatibility")
    juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked)

    if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
        db_leader = get_app_leader(juju, DB_APP_NAME)
        juju.run(
            unit=db_leader, action="force-refresh-start", params={"check-compatibility": "False"}
        )

        juju.wait(ready=jubilant.all_active)

    logging.info("Run resume-refresh action")
    units = get_app_units(juju, DB_APP_NAME)
    await juju.run(unit=units[sorted(units.keys())[1]], action="resume-refresh")

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    await check_db_units_writes_increment(juju, DB_APP_NAME)
