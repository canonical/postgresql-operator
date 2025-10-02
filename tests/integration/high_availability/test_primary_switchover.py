# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from subprocess import run

import pytest
from jubilant_backports import Juju, all_active

from .high_availability_helpers_new import (
    get_app_name,
    get_app_units,
    get_mysql_primary_unit,
    wait_for_unit_message,
    wait_for_unit_status,
)

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.mark.abort_on_fail
def test_cluster_switchover(juju: Juju, highly_available_cluster) -> None:
    """Test that the primary node can be switched over."""
    logging.info("Testing cluster switchover...")
    app_name = get_app_name(juju, "mysql")
    assert app_name, "MySQL application not found in the cluster"

    app_units = set(get_app_units(juju, app_name))
    assert len(app_units) > 1, "Not enough units to perform a switchover"

    primary_unit = get_mysql_primary_unit(juju, app_name)
    assert primary_unit, "No primary unit found in the cluster"
    logging.info(f"Current primary unit: {primary_unit}")

    logging.info("Selecting a new primary unit for switchover...")
    app_units.discard(primary_unit)
    new_primary_unit = app_units.pop()
    logging.info(f"New primary unit selected: {new_primary_unit}")

    switchover_task = juju.run(new_primary_unit, "promote-to-primary", {"scope": "unit"})
    assert switchover_task.status == "completed", "Switchover failed"

    assert get_mysql_primary_unit(juju, app_name) == new_primary_unit, "Switchover failed"


@pytest.mark.abort_on_fail
def test_cluster_failover_after_majority_loss(juju: Juju, highly_available_cluster) -> None:
    """Test the promote-to-primary command after losing the majority of nodes, with force flag."""
    app_name = get_app_name(juju, "mysql")
    assert app_name, "MySQL application not found in the cluster"

    app_units = set(get_app_units(juju, app_name))
    assert len(app_units) > 1, "Not enough units to perform a switchover"

    primary_unit = get_mysql_primary_unit(juju, app_name)
    assert primary_unit, "No primary unit found in the cluster"
    logging.info(f"Current primary unit: {primary_unit}")

    non_primary_units = app_units - {primary_unit}

    unit_to_promote = non_primary_units.pop()

    logging.info(f"Unit selected for promotion: {unit_to_promote}")

    logging.info("Kill all but one unit to simulate majority loss...")
    units_to_kill = [non_primary_units.pop(), primary_unit]
    machine_name = []
    for unit in units_to_kill:
        machine_name.append(get_unit_machine(juju, app_name, unit))

    run(["lxc", "restart", "--force", machine_name[0], machine_name[1]], check=True)

    juju.model_config({"update-status-hook-interval": "45s"})
    logging.info("Waiting to settle in error state")
    juju.wait(
        ready=lambda status: all((
            wait_for_unit_status(app_name, unit_to_promote, "active")(status),
            wait_for_unit_message(app_name, units_to_kill[0], "offline")(status),
            wait_for_unit_message(app_name, units_to_kill[1], "offline")(status),
        )),
        timeout=60 * 15,
        delay=15,
    )

    failover_task = juju.run(
        unit_to_promote,
        "promote-to-primary",
        {"scope": "unit", "force": True},
        wait=600,
    )

    juju.model_config({"update-status-hook-interval": "15s"})

    assert failover_task.status == "completed", "Switchover failed"
    logging.info("Waiting for all units to become active after switchover...")
    juju.wait(all_active, timeout=60 * 10, delay=5)

    assert get_mysql_primary_unit(juju, app_name) == unit_to_promote, "Failover failed"


def get_unit_machine(juju: Juju, app_name: str, unit_name: str) -> str:
    """Get the machine name for the given unit."""
    status = juju.status()
    machine_id = status.apps[app_name].units[unit_name].machine
    return status.machines[machine_id].instance_id
