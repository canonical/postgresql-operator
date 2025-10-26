#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import jubilant
import psycopg2

from .helpers import DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME
from .high_availability.high_availability_helpers_new import (
    get_app_leader,
    get_unit_ip,
    wait_for_apps_status,
)

MINUTE_SECS = 60


def test_deploy(juju: jubilant.Juju, charm) -> None:
    """Deploy the charms."""
    logging.info("Deploying database charm")
    juju.deploy(
        charm,
        app=DATABASE_APP_NAME,
        config={"profile": "testing"},
        num_units=1,
    )

    # Deploy the data integrator if not already deployed.
    logging.info("Deploying data integrator charm")
    juju.deploy(
        DATA_INTEGRATOR_APP_NAME,
        app="di1",
        channel="latest/edge",
        config={"database-name": "postgre1"},
    )
    juju.deploy(
        DATA_INTEGRATOR_APP_NAME,
        app="di2",
        channel="latest/edge",
        config={"database-name": "postgre2"},
    )

    creds_secret = juju.add_secret("creds-secret", {"tester": "password"})

    juju.deploy(
        DATA_INTEGRATOR_APP_NAME,
        app="di3",
        channel="latest/edge",
        config={"requested-entities-secret": creds_secret},
    )
    juju.grant_secret(creds_secret, "di3")

    logging.info("Relating data integrator")
    juju.integrate(f"{DATABASE_APP_NAME}:database", "di1")
    juju.integrate(f"{DATABASE_APP_NAME}:database", "di2")

    logging.info("Waiting for the applications to settle")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DATABASE_APP_NAME, "di1", "di2"),
        timeout=20 * MINUTE_SECS,
    )


def test_prefix_too_short(juju: jubilant.Juju) -> None:
    logging.info("Setting short prefix")
    juju.config("di3", {"database-name": "po*"})

    logging.info("Integrating with database")
    juju.integrate(f"{DATABASE_APP_NAME}:database", "di3")

    logging.info("Waiting for the applications to settle")
    db_leader = get_app_leader(juju, DATABASE_APP_NAME)
    juju.wait(
        ready=lambda status: status.apps[DATABASE_APP_NAME]
        .units[db_leader]
        .workload_status.message
        == "Prefix too short"
        and status.apps[DATABASE_APP_NAME].units[db_leader].is_blocked,
        timeout=5 * MINUTE_SECS,
    )

    juju.remove_relation(f"{DATABASE_APP_NAME}", "di3")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)


def test_get_prefix(juju: jubilant.Juju) -> None:
    db_ip = get_unit_ip(juju, DATABASE_APP_NAME, get_app_leader(juju, DATABASE_APP_NAME))

    logging.info("Setting prefix")
    juju.config("di3", {"database-name": "postgre*"})

    logging.info("Integrating with database")
    juju.integrate(f"{DATABASE_APP_NAME}:database", "di3")

    logging.info("Waiting for the applications to settle")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DATABASE_APP_NAME, "di1", "di2", "di3"),
        timeout=5 * MINUTE_SECS,
    )

    integrator_leader = get_app_leader(juju, "di3")
    result = juju.run(unit=integrator_leader, action="get-credentials")
    result.raise_on_failure()

    # Assert credentials and prefix
    pg_data = result.results["postgresql"]
    assert "postgres" not in pg_data["prefix-databases"]
    assert pg_data["prefix-databases"] == "postgre1,postgre2"
    assert pg_data["username"] == "tester"
    assert pg_data["password"] == "password"
    assert pg_data["uris"] == f"postgresql://tester:password@{db_ip}:5432/postgre1"

    # Connect to database
    psycopg2.connect(f"postgresql://tester:password@{db_ip}:5432/postgre1")
    psycopg2.connect(f"postgresql://tester:password@{db_ip}:5432/postgre2")


def test_remove_db_from_prefix(juju: jubilant.Juju) -> None:
    db_ip = get_unit_ip(juju, DATABASE_APP_NAME, get_app_leader(juju, DATABASE_APP_NAME))

    juju.remove_relation(f"{DATABASE_APP_NAME}", "di1")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    integrator_leader = get_app_leader(juju, "di3")
    result = juju.run(unit=integrator_leader, action="get-credentials")
    result.raise_on_failure()

    pg_data = result.results["postgresql"]
    assert "postgres" not in pg_data["prefix-databases"]
    assert pg_data["prefix-databases"] == "postgre2"
    assert pg_data["uris"] == f"postgresql://tester:password@{db_ip}:5432/postgre2"

    # Connect to database
    # TODO this should fail
    psycopg2.connect(f"postgresql://tester:password@{db_ip}:5432/postgre1")
    psycopg2.connect(f"postgresql://tester:password@{db_ip}:5432/postgre2")


def test_readd_db_from_prefix(juju: jubilant.Juju) -> None:
    db_ip = get_unit_ip(juju, DATABASE_APP_NAME, get_app_leader(juju, DATABASE_APP_NAME))

    juju.integrate(f"{DATABASE_APP_NAME}", "di1")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    integrator_leader = get_app_leader(juju, "di3")
    result = juju.run(unit=integrator_leader, action="get-credentials")
    result.raise_on_failure()

    pg_data = result.results["postgresql"]
    assert "postgres" not in pg_data["prefix-databases"]
    assert pg_data["prefix-databases"] == "postgre1,postgre2"
    assert pg_data["uris"] == f"postgresql://tester:password@{db_ip}:5432/postgre1"

    # Connect to database
    psycopg2.connect(f"postgresql://tester:password@{db_ip}:5432/postgre1")
    psycopg2.connect(f"postgresql://tester:password@{db_ip}:5432/postgre2")
