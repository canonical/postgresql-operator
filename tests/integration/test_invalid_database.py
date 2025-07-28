#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import jubilant
import pytest as pytest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .helpers import (
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
)
from .jubilant_helpers import relations

logger = logging.getLogger(__name__)

REQUESTED_DATABASE_NAME = "requested-database"
RELATION_ENDPOINT = "postgresql"
TIMEOUT = 15 * 60


def data_integrator_blocked(status: jubilant.Status, app_name=DATA_INTEGRATOR_APP_NAME) -> bool:
    return jubilant.all_blocked(status, app_name)


def database_active(status: jubilant.Status, app_name=DATABASE_APP_NAME) -> bool:
    return jubilant.all_active(status, app_name)


@pytest.mark.abort_on_fail
def test_deploy(juju: jubilant.Juju, charm) -> None:
    """Deploy the charms."""
    # Deploy the database charm if not already deployed.
    if DATABASE_APP_NAME not in juju.status().apps:
        logger.info("Deploying database charm")
        juju.deploy(
            charm,
            config={"profile": "testing"},
            num_units=1,
        )

    # Deploy the data integrator if not already deployed.
    if DATA_INTEGRATOR_APP_NAME not in juju.status().apps:
        logger.info("Deploying data integrator charm")
        juju.deploy(
            DATA_INTEGRATOR_APP_NAME,
            config={"database-name": REQUESTED_DATABASE_NAME},
        )

    # Relate the data integrator charm to the database charm.
    existing_relations = relations(juju, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME)
    if existing_relations:
        logger.info("Removing existing relation between charms")
        juju.remove_relation(f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME)
        juju.wait(lambda status: data_integrator_blocked(status), timeout=TIMEOUT)

    logger.info(
        "Waiting for the database charm to become active and the data integrator charm to block"
    )
    juju.wait(lambda status: database_active(status), timeout=TIMEOUT)
    juju.wait(lambda status: data_integrator_blocked(status), timeout=TIMEOUT)


def test_database(juju: jubilant.Juju, predefined_roles) -> None:
    """Check that an invalid database name makes the database charm block."""
    del predefined_roles[""]
    invalid_database_names = [
        "postgres",
        "template0",
        "template1",
    ]
    logger.info(f"Invalid database names: {invalid_database_names}")

    for invalid_database_name in invalid_database_names:
        logger.info(f"Requesting invalid database name: {invalid_database_name}")
        juju.config(
            app=DATA_INTEGRATOR_APP_NAME,
            values={
                "database-name": invalid_database_name,
            },
        )
        juju.wait(lambda status: data_integrator_blocked(status), timeout=TIMEOUT)

        logger.info("Adding relation between charms")
        for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(5)):
            with attempt:
                juju.integrate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)

        logger.info("Waiting for the database charm to block due to invalid database name")

        def all_units_blocked(status: jubilant.Status) -> bool:
            for app in status.apps:
                for unit_info in status.get_units(app).values():
                    if unit_info.workload_status.current != "blocked":
                        return False
            return True

        juju.wait(lambda status: all_units_blocked(status))
        assert (
            juju.status()
            .get_units(DATABASE_APP_NAME)
            .get(f"{DATABASE_APP_NAME}/0")
            .workload_status.message
            == "invalid database name"
        ), "The database charm didn't block as expected due to invalid database name."

        logger.info("Removing relation between charms")
        juju.remove_relation(f"{DATA_INTEGRATOR_APP_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME)

        logger.info("Waiting for the database charm to become active again")
        juju.wait(lambda status: database_active(status), timeout=TIMEOUT)
        juju.wait(lambda status: data_integrator_blocked(status), timeout=TIMEOUT)
