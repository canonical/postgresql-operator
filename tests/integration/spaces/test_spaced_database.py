# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from time import sleep

import jubilant
import pytest

PG_NAME = "postgresql"
APP_NAME = "postgresql-test-app"
ISOLATED_APP_NAME = "isolated-app"
TIMEOUT = 60 * 15


logger = logging.getLogger(__name__)


def test_deploy(juju: jubilant.Juju, lxd_spaces, charm):
    """Deploy the charm with the LXD spaces."""
    # Deploy the charm with the LXD spaces
    # TODO: use juju.deploy once bind is supported
    juju.cli(
        "deploy",
        charm,
        PG_NAME,
        "--constraints",
        "spaces=client,peers",
        "--bind",
        "database_peers=peers",
        "--bind",
        "database=client",
        "-n",
        "3",
    )

    juju.deploy(
        APP_NAME,
        app=APP_NAME,
        channel="latest/edge",
        constraints={"spaces": "client"},
        config={"sleep_interval": 1000},
    )
    # Wait for the deployment to complete
    juju.wait(
        lambda status: jubilant.all_active(status),
        timeout=TIMEOUT,
        delay=10,
        successes=3,
    )
    # bind after app is up
    juju.cli("bind", APP_NAME, "database=client")


def test_integrate_with_spaces(juju: jubilant.Juju):
    """Relate the database to the application."""
    # Relate the database to the application
    juju.integrate(PG_NAME, f"{APP_NAME}:database")

    sleep(10)
    # Wait for the relation to be established
    logger.info("Waiting for relation to be established")
    juju.wait(lambda status: status.apps[PG_NAME].is_active, delay=10)

    status = juju.status()

    unit = next(iter(status.apps[APP_NAME].units))
    logger.info(f"Check for continuous writes on {unit=}")
    juju.run(unit, "start-continuous-writes")
    sleep(10)
    task = juju.run(unit, "show-continuous-writes")

    assert task.status == "completed", "Show continuous writes failed"
    assert int(task.results["writes"]) > 0, "Show continuous writes failed"
    juju.remove_application(APP_NAME)


def test_integrate_with_isolated_space(juju: jubilant.Juju):
    """Relate the database to the application."""
    juju.deploy(
        APP_NAME,
        app=ISOLATED_APP_NAME,
        channel="latest/edge",
        constraints={"spaces": "isolated"},
    )
    juju.wait(lambda status: status.apps[ISOLATED_APP_NAME].is_active)
    juju.cli("bind", ISOLATED_APP_NAME, "database=isolated")

    # Relate the database to the application
    juju.integrate(PG_NAME, f"{ISOLATED_APP_NAME}:database")
    sleep(10)
    # Wait for the relation to be established
    juju.wait(lambda status: status.apps[PG_NAME].is_active, delay=10)

    status = juju.status()
    unit = next(iter(status.apps[ISOLATED_APP_NAME].units))
    # remove default route on client so traffic cant be routed through default interface
    juju.exec("sudo ip route flush default", unit=unit)

    with pytest.raises(jubilant.TaskError):
        juju.run(unit, "start-continuous-writes")
