#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess

import jubilant
import psycopg2
import pytest

from .helpers import DATABASE_APP_NAME
from .jubilant_helpers import get_credentials

logger = logging.getLogger(__name__)

TIMEOUT = 20 * 60
RELATION_ENDPOINT = "postgresql"
DATA_INTEGRATOR_APP_NAME = "data-integrator"


@pytest.mark.abort_on_fail
def test_deploy_with_tmpfs_storage(juju: jubilant.Juju, charm) -> None:
    """Deploy PostgreSQL with tmpfs temp storage and data-integrator."""
    # Deploy database app with tmpfs for temporary storage.
    if DATABASE_APP_NAME not in juju.status().apps:
        logger.info("Deploying PostgreSQL with tmpfs temporary storage")
        juju.deploy(
            charm,
            app=DATABASE_APP_NAME,
            num_units=1,
            config={"profile": "testing"},
            storage={"temp": "5G,tmpfs"},
        )

    # Deploy data-integrator to get credentials.
    if DATA_INTEGRATOR_APP_NAME not in juju.status().apps:
        logger.info("Deploying data-integrator")
        juju.deploy(DATA_INTEGRATOR_APP_NAME, config={"database-name": "test"})

    # Relate if not already related
    status = juju.status()
    if not status.apps[DATABASE_APP_NAME].relations.get(RELATION_ENDPOINT):
        juju.integrate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)

    logger.info("Waiting for both applications to become active")
    juju.wait(
        lambda s: jubilant.all_active(s, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
        timeout=TIMEOUT,
    )


def test_restart_and_temp_table(juju: jubilant.Juju) -> None:
    """Restart the LXD machine and verify TEMP TABLE creation works afterwards."""
    unit_name = f"{DATABASE_APP_NAME}/0"

    # Find machine name and restart
    status = juju.status()
    unit_info = status.get_units(unit_name.split("/")[0]).get(unit_name)
    machine_name = None
    if unit_info:
        machine_id = getattr(unit_info, "machine", None)
        if machine_id:
            # Look up the machine object in status and try common attributes that hold the LXD name
            machine_obj = (
                getattr(status, "machines", {}).get(machine_id)
                if hasattr(status, "machines")
                else None
            )
            if machine_obj:
                machine_name = getattr(machine_obj, "instance_id", None)

    if machine_name is None:
        raise RuntimeError("Unable to determine LXD machine/container name for unit " + unit_name)

    logger.info(f"Restarting LXD machine {machine_name}")
    subprocess.check_call(["lxc", "restart", machine_name])

    # Wait for unit to go active/idle again
    logger.info("Waiting for PostgreSQL unit to become active after restart")
    juju.wait(
        lambda s: jubilant.all_active(s, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
        delay=30,
        timeout=TIMEOUT,
    )

    # Obtain credentials via data-integrator action
    creds = get_credentials(juju, f"{DATA_INTEGRATOR_APP_NAME}/0")
    uri = creds[RELATION_ENDPOINT]["uris"]

    # Connect and create a TEMPORARY TABLE
    connection = None
    try:
        connection = psycopg2.connect(uri)
        connection.autocommit = True
        with connection.cursor() as cur:
            cur.execute("CREATE TEMPORARY TABLE test (lines TEXT);")
    finally:
        if connection is not None:
            connection.close()
