#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Regression test for DPE-10284.

A standby (read-only) cluster whose units are all restarted (a full outage) must
not crash the standby leader. The standby leader's re-emitted start hook used to
run user/role setup (``CREATE EXTENSION ...``) against the read-only instance and
fail with ``ReadOnlySqlTransaction``, leaving the unit stuck in error even though
replication was healthy.
"""

import logging
import subprocess
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju

from .. import architecture
from .high_availability_helpers_new import (
    get_app_leader,
    get_db_standby_leader_unit,
    wait_for_apps_status,
)

DB_APP_1 = "db1"
DB_APP_2 = "db2"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def first_model(juju: Juju) -> Generator:
    """Return the first (primary cluster) model."""
    yield juju.model


@pytest.fixture(scope="module")
def second_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Create and return the second (standby cluster) model."""
    model_name = f"{juju.model}-other"

    logging.info(f"Creating model: {model_name}")
    juju.add_model(model_name)

    yield model_name
    if request.config.getoption("--keep-models"):
        return

    logging.info(f"Destroying model: {model_name}")
    juju.destroy_model(model_name, destroy_storage=True, force=True)


def test_deploy(first_model: str, second_model: str, charm: str) -> None:
    """Deploy a PostgreSQL cluster in each model."""
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}

    model_1 = Juju(model=first_model)
    model_1.deploy(
        charm=charm,
        app=DB_APP_1,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=2,
    )
    model_2 = Juju(model=second_model)
    model_2.deploy(
        charm=charm,
        app=DB_APP_2,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=2,
    )

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
    )


def test_async_relate_and_replicate(first_model: str, second_model: str) -> None:
    """Relate the two clusters and start replication, making db2 a standby cluster."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    model_1.offer(f"{first_model}.{DB_APP_1}", endpoint="replication-offer")
    model_2.consume(f"{first_model}.{DB_APP_1}")
    model_2.integrate(DB_APP_1, f"{DB_APP_2}:replication")

    # Wait for the relation to settle before create-replication: the action fails unless
    # every unit has published its address in the relation data.
    model_1.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_1), timeout=10 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_2), timeout=10 * MINUTE_SECS
    )

    model_1.run(
        unit=get_app_leader(model_1, DB_APP_1), action="create-replication", wait=5 * MINUTE_SECS
    ).raise_on_failure()

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
    )

    # db2 must now be the read-only standby cluster.
    assert get_db_standby_leader_unit(model_2, DB_APP_2)


def test_full_outage_recovery(first_model: str, second_model: str) -> None:
    """Stop every unit on both clusters, start them again, and assert recovery.

    Reproduces DPE-10284: before the fix, the standby leader's start hook failed
    with ReadOnlySqlTransaction and the unit stayed in error forever.
    """
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    # Resolve every unit's LXD machine (container) while the units are reachable.
    machines = []
    for model, app in ((model_1, DB_APP_1), (model_2, DB_APP_2)):
        status = model.status()
        machines.extend(
            status.machines[unit.machine].instance_id for unit in status.get_units(app).values()
        )

    logging.info(f"Simulating a full outage by force-stopping all machines: {machines}")
    for machine in machines:
        subprocess.run(["lxc", "stop", "--force", machine], check=True)
    for machine in machines:
        subprocess.run(["lxc", "start", machine], check=True)

    logging.info("Waiting for both clusters to recover")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
    )
    # If the standby leader crashes its start hook (DPE-10284), db2 never reaches
    # all-active and this wait fails.
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
    )

    # The standby cluster must still expose a standby leader after recovery.
    assert get_db_standby_leader_unit(model_2, DB_APP_2)
