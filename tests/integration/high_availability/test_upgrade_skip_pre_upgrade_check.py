# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
import pytest
from jubilant_backports import Juju

from .high_availability_helpers_new import (
    check_mysql_units_writes_increment,
    get_app_units,
    wait_for_apps_status,
    wait_for_unit_status,
)

MYSQL_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.mark.abort_on_fail
def test_deploy_stable(juju: Juju) -> None:
    """Simple test to ensure that the MySQL and application charms get deployed."""
    logging.info("Deploying MySQL cluster")
    juju.deploy(
        charm=MYSQL_APP_NAME,
        app=MYSQL_APP_NAME,
        base="ubuntu@22.04",
        channel="8.0/stable",
        config={"profile": "testing"},
        num_units=3,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        config={"sleep_interval": 50},
        num_units=1,
    )

    juju.integrate(
        f"{MYSQL_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active, MYSQL_APP_NAME, MYSQL_TEST_APP_NAME
        ),
        error=jubilant_backports.any_blocked,
        timeout=20 * MINUTE_SECS,
    )


@pytest.mark.abort_on_fail
async def test_refresh_without_pre_upgrade_check(juju: Juju, charm: str) -> None:
    """Test updating from stable channel."""
    logging.info("Refresh the charm")
    juju.refresh(app=MYSQL_APP_NAME, path=charm)

    logging.info("Wait for rolling restart")
    app_units = get_app_units(juju, MYSQL_APP_NAME)
    app_units_funcs = [wait_for_unit_status(MYSQL_APP_NAME, unit, "error") for unit in app_units]

    juju.wait(
        ready=lambda status: any(status_func(status) for status_func in app_units_funcs),
        timeout=10 * MINUTE_SECS,
        successes=1,
    )

    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME)


@pytest.mark.abort_on_fail
async def test_rollback_without_pre_upgrade_check(juju: Juju, charm: str) -> None:
    """Test refresh back to stable channel."""
    # Early Jubilant 1.X.Y versions do not support the `switch` option
    logging.info("Refresh the charm to stable channel")
    juju.cli("refresh", "--channel=8.0/stable", f"--switch={MYSQL_APP_NAME}", MYSQL_APP_NAME)

    logging.info("Wait for rolling restart")
    app_units = get_app_units(juju, MYSQL_APP_NAME)
    app_units_funcs = [wait_for_unit_status(MYSQL_APP_NAME, unit, "error") for unit in app_units]

    juju.wait(
        ready=lambda status: any(status_func(status) for status_func in app_units_funcs),
        timeout=10 * MINUTE_SECS,
        successes=1,
    )

    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME)
