#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from collections.abc import Callable

import jubilant
from jubilant import Juju
from jubilant.statustypes import Status, UnitStatus

MINUTE_SECS = 60

JujuModelStatusFn = Callable[[Status], bool]
JujuAppsStatusFn = Callable[[Status, str], bool]


def get_app_leader(juju: Juju, app_name: str) -> str:
    """Get the leader unit for the given application."""
    app_status = juju.status().apps[app_name]
    for name, status in app_status.units.items():
        if status.leader:
            return name

    raise Exception("No leader unit found")


def get_app_units(juju: Juju, app_name: str) -> dict[str, UnitStatus]:
    """Get the units for the given application."""
    return juju.status().apps[app_name].units


def get_db_primary_unit(juju: Juju, app_name: str) -> str:
    """Get the current primary node of the cluster."""
    postgresql_primary = get_app_leader(juju, app_name)
    task = juju.run(unit=postgresql_primary, action="get-primary", wait=5 * MINUTE_SECS)
    task.raise_on_failure()

    primary = task.results.get("primary")
    if primary != "None":
        return primary

    raise Exception("No primary node found")


def wait_for_apps_status(jubilant_status_func: JujuAppsStatusFn, *apps: str) -> JujuModelStatusFn:
    """Wait for Juju agents to be idle and apps to reach a target status."""
    return lambda status: all((
        jubilant.all_agents_idle(status, *apps),
        jubilant_status_func(status, *apps),
    ))
