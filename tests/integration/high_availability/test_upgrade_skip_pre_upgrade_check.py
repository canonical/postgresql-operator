# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju

from .high_availability_helpers_new import (
    check_db_units_writes_increment,
    count_switchovers,
    get_app_units,
    wait_for_apps_status,
    wait_for_unit_status,
)

DB_APP_NAME = "postgresql"
DB_TEST_APP_NAME = "postgresql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


def test_deploy_stable(juju: Juju) -> None:
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


def test_refresh_without_pre_upgrade_check(juju: Juju, charm: str) -> None:
    """Test updating from stable channel."""
    initial_number_of_switchovers = count_switchovers(juju, DB_APP_NAME)

    logging.info("Refresh the charm")
    juju.refresh(app=DB_APP_NAME, path=charm)

    logging.info("Wait for refresh to block as paused or incompatible")
    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=5 * MINUTE_SECS)

        units = get_app_units(juju, DB_APP_NAME)
        unit_names = sorted(units.keys())

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
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
        assert juju.status().apps[DB_APP_NAME].is_active

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_db_units_writes_increment(juju, DB_APP_NAME)

    logging.info("checking the number of switchovers")
    final_number_of_switchovers = count_switchovers(juju, DB_APP_NAME)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
        "Number of switchovers is greater than 2"
    )


async def test_rollback_without_pre_upgrade_check(juju: Juju, charm: str) -> None:
    """Test refresh back to stable channel."""
    # Early Jubilant 1.X.Y versions do not support the `switch` option
    logging.info("Refresh the charm to stable channel")
    juju.cli("refresh", "--channel=16/stable", f"--switch={DB_APP_NAME}", DB_APP_NAME)

    logging.info("Wait for rolling restart")
    app_units = get_app_units(juju, DB_APP_NAME)
    app_units_funcs = [wait_for_unit_status(DB_APP_NAME, unit, "error") for unit in app_units]

    juju.wait(
        ready=lambda status: any(status_func(status) for status_func in app_units_funcs),
        timeout=10 * MINUTE_SECS,
        successes=1,
    )

    check_db_units_writes_increment(juju, DB_APP_NAME)
