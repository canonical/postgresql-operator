#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess
from collections.abc import Callable

import jubilant_backports
from jubilant_backports import Juju
from jubilant_backports.statustypes import Status, UnitStatus
from tenacity import Retrying, stop_after_delay, wait_fixed

from constants import SERVER_CONFIG_USERNAME

from ..helpers import execute_queries_on_unit

MINUTE_SECS = 60

JujuModelStatusFn = Callable[[Status], bool]
JujuAppsStatusFn = Callable[[Status, str], bool]


async def check_mysql_units_writes_increment(
    juju: Juju, app_name: str, app_units: list[str] | None = None
) -> None:
    """Ensure that continuous writes is incrementing on all units.

    Also, ensure that all continuous writes up to the max written value is available
    on all units (ensure that no committed data is lost).
    """
    if not app_units:
        app_units = get_app_units(juju, app_name)

    app_primary = get_mysql_primary_unit(juju, app_name)
    app_max_value = await get_mysql_max_written_value(juju, app_name, app_primary)

    juju.model_config({"update-status-hook-interval": "15s"})
    for unit_name in app_units:
        for attempt in Retrying(
            reraise=True,
            stop=stop_after_delay(5 * MINUTE_SECS),
            wait=wait_fixed(10),
        ):
            with attempt:
                unit_max_value = await get_mysql_max_written_value(juju, app_name, unit_name)
                assert unit_max_value > app_max_value, "Writes not incrementing"
                app_max_value = unit_max_value


def get_app_leader(juju: Juju, app_name: str) -> str:
    """Get the leader unit for the given application."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    for name, status in app_status.units.items():
        if status.leader:
            return name

    raise Exception("No leader unit found")


def get_app_name(juju: Juju, charm_name: str) -> str | None:
    """Get the application name for the given charm."""
    model_status = juju.status()
    app_statuses = model_status.apps
    for name, status in app_statuses.items():
        if status.charm_name == charm_name:
            return name

    raise Exception("No application name found")


def get_app_units(juju: Juju, app_name: str) -> dict[str, UnitStatus]:
    """Get the units for the given application."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    return app_status.units


def get_unit_by_number(juju: Juju, app_name: str, unit_number: int) -> str:
    """Get unit by number."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    for name in app_status.units:
        if name == f"{app_name}/{unit_number}":
            return name

    raise Exception("No application unit found")


def get_unit_ip(juju: Juju, app_name: str, unit_name: str) -> str:
    """Get the application unit IP."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    for name, status in app_status.units.items():
        if name == unit_name:
            return status.public_address

    raise Exception("No application unit found")


def get_unit_info(juju: Juju, unit_name: str) -> dict:
    """Return a dictionary with the show-unit data."""
    output = subprocess.check_output(
        ["juju", "show-unit", f"--model={juju.model}", "--format=json", unit_name],
        text=True,
    )

    return json.loads(output)


def get_unit_status_log(juju: Juju, unit_name: str, log_lines: int = 0) -> list[dict]:
    """Get the status log for a unit.

    Args:
        juju: The juju instance to use.
        unit_name: The name of the unit to retrieve the status log for
        log_lines: The number of status logs to retrieve (optional)
    """
    # fmt: off
    output = subprocess.check_output(
        ["juju", "show-status-log", f"--model={juju.model}", "--format=json", unit_name, "-n", f"{log_lines}"],
        text=True,
    )

    return json.loads(output)


def get_relation_data(juju: Juju, app_name: str, rel_name: str) -> list[dict]:
    """Returns a list that contains the relation-data.

    Args:
        juju: The juju instance to use.
        app_name: The name of the application
        rel_name: name of the relation to get connection data from

    Returns:
        A list that contains the relation-data
    """
    app_leader = get_app_leader(juju, app_name)
    app_leader_info = get_unit_info(juju, app_leader)
    if not app_leader_info:
        raise ValueError(f"No unit info could be grabbed for unit {app_leader}")

    relation_data = [
        value
        for value in app_leader_info[app_leader]["relation-info"]
        if value["endpoint"] == rel_name
    ]
    if not relation_data:
        raise ValueError(f"No relation data could be grabbed for relation {rel_name}")

    return relation_data


def get_mysql_cluster_status(juju: Juju, unit: str, cluster_set: bool = False) -> dict:
    """Get the cluster status by running the get-cluster-status action.

    Args:
        juju: The juju instance to use.
        unit: The unit on which to execute the action on
        cluster_set: Whether to get the cluster-set instead (optional)

    Returns:
        A dictionary representing the cluster status
    """
    task = juju.run(
        unit=unit,
        action="get-cluster-status",
        params={"cluster-set": cluster_set},
        wait=5 * MINUTE_SECS,
    )
    task.raise_on_failure()

    return task.results.get("status", {})


def get_mysql_unit_name(instance_label: str) -> str:
    """Builds a Juju unit name out of a MySQL instance label."""
    return "/".join(instance_label.rsplit("-", 1))


def get_mysql_primary_unit(juju: Juju, app_name: str) -> str:
    """Get the current primary node of the cluster."""
    mysql_primary = get_app_leader(juju, app_name)
    mysql_cluster_status = get_mysql_cluster_status(juju, mysql_primary)
    mysql_cluster_topology = mysql_cluster_status["defaultreplicaset"]["topology"]

    for label, value in mysql_cluster_topology.items():
        if value["memberrole"] == "primary":
            return get_mysql_unit_name(label)

    raise Exception("No MySQL primary node found")


async def get_mysql_max_written_value(juju: Juju, app_name: str, unit_name: str) -> int:
    """Retrieve the max written value in the MySQL database.

    Args:
        juju: The Juju model.
        app_name: The application name.
        unit_name: The unit name.
    """
    credentials_task = juju.run(
        unit=unit_name,
        action="get-password",
        params={"username": SERVER_CONFIG_USERNAME},
    )
    credentials_task.raise_on_failure()

    output = await execute_queries_on_unit(
        get_unit_ip(juju, app_name, unit_name),
        credentials_task.results["username"],
        credentials_task.results["password"],
        ["SELECT MAX(number) FROM `continuous_writes`.`data`;"],
    )
    return output[0]


async def get_mysql_variable_value(
    juju: Juju, app_name: str, unit_name: str, variable_name: str
) -> str:
    """Retrieve a database variable value as a string.

    Args:
        juju: The Juju model.
        app_name: The application name.
        unit_name: The unit name.
        variable_name: The variable name.
    """
    credentials_task = juju.run(
        unit=unit_name,
        action="get-password",
        params={"username": SERVER_CONFIG_USERNAME},
    )
    credentials_task.raise_on_failure()

    output = await execute_queries_on_unit(
        get_unit_ip(juju, app_name, unit_name),
        credentials_task.results["username"],
        credentials_task.results["password"],
        [f"SELECT @@{variable_name};"],
    )
    return output[0]


def wait_for_apps_status(jubilant_status_func: JujuAppsStatusFn, *apps: str) -> JujuModelStatusFn:
    """Waits for Juju agents to be idle, and for applications to reach a certain status.

    Args:
        jubilant_status_func: The Juju apps status function to wait for.
        apps: The applications to wait for.

    Returns:
        Juju model status function.
    """
    return lambda status: all((
        jubilant_backports.all_agents_idle(status, *apps),
        jubilant_status_func(status, *apps),
    ))


def wait_for_unit_status(app_name: str, unit_name: str, unit_status: str) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific status."""
    return lambda status: (
        status.apps[app_name].units[unit_name].workload_status.current == unit_status
    )


def wait_for_unit_message(app_name: str, unit_name: str, unit_message: str) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific message."""
    return lambda status: (
        status.apps[app_name].units[unit_name].workload_status.message == unit_message
    )
