#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess

import jubilant
import pytest

from .helpers import DATABASE_APP_NAME
from .jubilant_helpers import (
    check_for_fix_log_message,
    force_leader_election,
    get_lxd_machine_name,
    verify_leader_active,
    verify_temp_table_creation,
)

logger = logging.getLogger(__name__)

TIMEOUT = 20 * 60  # 20 minutes
DATA_INTEGRATOR_APP_NAME = "data-integrator"


@pytest.mark.abort_on_fail
def test_deploy_with_persistent_temp_storage(juju: jubilant.Juju, charm) -> None:
    """Deploy PostgreSQL with 3 units using default persistent storage and data-integrator.

    Required: subsequent test depends on successful deployment.
    """
    # Deploy database app with default storage (persistent, no storage directive).
    if DATABASE_APP_NAME not in juju.status().apps:
        logger.info("Deploying PostgreSQL with 3 units (default persistent storage)")
        juju.deploy(
            charm,
            app=DATABASE_APP_NAME,
            num_units=3,
            config={"profile": "testing"},
        )

    # Deploy data-integrator to get credentials.
    if DATA_INTEGRATOR_APP_NAME not in juju.status().apps:
        logger.info("Deploying data-integrator")
        juju.deploy(DATA_INTEGRATOR_APP_NAME, config={"database-name": "test"})

    # Relate if not already related
    status = juju.status()
    db_relations = status.apps[DATABASE_APP_NAME].relations.get("database", [])
    is_related = any(rel.related_app == DATA_INTEGRATOR_APP_NAME for rel in db_relations)
    if not is_related:
        logger.info(f"Integrating {DATA_INTEGRATOR_APP_NAME} with {DATABASE_APP_NAME}")
        juju.integrate(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)

    logger.info("Waiting for all applications to become active")
    juju.wait(
        lambda s: jubilant.all_active(s, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
        timeout=TIMEOUT,
    )


def test_leader_change_and_restart(juju: jubilant.Juju) -> None:
    """Force leader change and restart to trigger the library fix code path.

    This test properly validates the fix from
    https://github.com/canonical/postgresql-single-kernel-library/pull/54 by:
    1. Finding the current Juju leader
    2. Killing the leader's juju agent to force leader election
    3. Waiting for a new leader to be elected
    4. Restarting the new leader's LXD machine
    5. Verifying the log message appears confirming the fix handled persistent temp storage

    This scenario triggers set_up_database() on the new leader during the start event,
    which should detect the persistent temp storage and log the appropriate message.
    """
    # Find the current Juju leader
    logger.info("Finding current Juju leader unit")
    status = juju.status()
    original_leader = None
    non_leader_units = []

    for unit_name, unit_status in status.get_units(DATABASE_APP_NAME).items():
        if unit_status.leader:
            original_leader = unit_name
        else:
            non_leader_units.append(unit_name)

    if original_leader is None:
        raise RuntimeError("Unable to find Juju leader unit")
    if len(non_leader_units) < 1:
        raise RuntimeError("Need at least one non-leader unit for this test")

    logger.info(f"Original Juju leader is: {original_leader}")
    logger.info(f"Non-leader units: {non_leader_units}")

    # Force leader election
    new_leader = force_leader_election(juju, original_leader)

    # Wait for cluster to stabilize
    logger.info("Waiting for cluster to stabilize after leader election")
    juju.wait(
        lambda s: jubilant.all_active(s, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
        delay=10,
        timeout=TIMEOUT,
    )

    # Now restart the new leader's LXD machine
    # This should trigger set_up_database() which will detect persistent temp storage
    new_leader_machine = get_lxd_machine_name(juju.status(), new_leader)
    logger.info(f"Restarting LXD machine {new_leader_machine} for new leader {new_leader}")
    subprocess.check_call(["lxc", "restart", new_leader_machine])

    # Wait for cluster to stabilize after restart
    logger.info("Waiting for cluster to stabilize after new leader restart")
    juju.wait(
        lambda s: jubilant.all_active(s, DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
        delay=30,
        timeout=TIMEOUT,
    )

    # Verify the new leader is active
    logger.info(f"Verifying that new leader {new_leader} is active after restart")
    status = juju.status()
    verify_leader_active(status, new_leader)
    logger.info(f"New leader {new_leader} is active after restart")

    # Check for the log message that confirms the fix is working
    check_for_fix_log_message(juju, new_leader)

    # Test temporary table creation
    logger.info("Testing temporary table creation on database")
    verify_temp_table_creation(juju)
