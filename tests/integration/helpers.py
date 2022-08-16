#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
from typing import List

import psycopg2
import requests
import yaml
from juju.unit import Unit
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def build_application_name(series: str) -> str:
    """Return a composite application name combining application name and series."""
    return f"{APP_NAME}-{series}"


async def check_cluster_members(ops_test: OpsTest, application_name: str) -> None:
    """Check that the correct members are part of the cluster.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
    """
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            any_unit_name = ops_test.model.applications[application_name].units[0].name
            primary = await get_primary(ops_test, any_unit_name)
            address = get_unit_address(ops_test, primary)

            expected_members = get_application_units(ops_test, application_name)
            expected_members_ips = get_application_units_ips(ops_test, application_name)

            r = requests.get(f"http://{address}:8008/cluster")
            assert [member["name"] for member in r.json()["members"]] == expected_members
            assert [member["host"] for member in r.json()["members"]] == expected_members_ips


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    dict = {}
    for record in records:
        # Add record tuple data to dict.
        dict[record[0]] = record[1]
    return dict


def db_connect(host: str, password: str) -> psycopg2.extensions.connection:
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host
        password: operator user password

    Returns:
        psycopg2 connection object linked to postgres db, under "operator" user.
    """
    return psycopg2.connect(
        f"dbname='postgres' user='operator' host='{host}' password='{password}' connect_timeout=10"
    )


async def find_unit(ops_test: OpsTest, application: str, leader: bool) -> Unit:
    """Helper function that retrieves a unit, based on need for leader or non-leader.

    Args:
        ops_test: The ops test framework instance.
        application: The name of the application.
        leader: Whether the unit is a leader or not.

    Returns:
        A unit instance.
    """
    ret_unit = None
    for unit in ops_test.model.applications[application].units:
        if await unit.is_leader_from_status() == leader:
            ret_unit = unit

    return ret_unit


def get_application_units(ops_test: OpsTest, application_name: str) -> List[str]:
    """List the unit names of an application.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application

    Returns:
        list of current unit names of the application
    """
    return [
        unit.name.replace("/", "-") for unit in ops_test.model.applications[application_name].units
    ]


def get_application_units_ips(ops_test: OpsTest, application_name: str) -> List[str]:
    """List the unit IPs of an application.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application

    Returns:
        list of current unit IPs of the application
    """
    return [unit.public_address for unit in ops_test.model.applications[application_name].units]


async def get_operator_password(ops_test: OpsTest, unit_name: str) -> str:
    """Retrieve the operator user password using the action.

    Args:
        ops_test: ops_test instance.
        unit_name: the name of the unit.

    Returns:
        the operator user password.
    """
    unit = ops_test.model.units.get(unit_name)
    action = await unit.run_action("get-operator-password")
    result = await action.wait()
    return result.results["operator-password"]


async def get_primary(ops_test: OpsTest, unit_name: str) -> str:
    """Get the primary unit.

    Args:
        ops_test: ops_test instance.
        unit_name: the name of the unit.

    Returns:
        the current primary unit.
    """
    action = await ops_test.model.units.get(unit_name).run_action("get-primary")
    action = await action.wait()
    return action.results["primary"]


def get_unit_address(ops_test: OpsTest, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    return ops_test.model.units.get(unit_name).public_address


async def scale_application(ops_test: OpsTest, application_name: str, count: int) -> None:
    """Scale a given application to a specific unit count.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        count: The desired number of units to scale to
    """
    change = count - len(ops_test.model.applications[application_name].units)
    if change > 0:
        await ops_test.model.applications[application_name].add_units(change)
    elif change < 0:
        units = [
            unit.name for unit in ops_test.model.applications[application_name].units[0:-change]
        ]
        await ops_test.model.applications[application_name].destroy_units(*units)
    await ops_test.model.wait_for_idle(
        apps=[application_name], status="active", timeout=1000, wait_for_exact_units=count
    )


def switchover(ops_test: OpsTest, current_primary: str, candidate: str = None) -> None:
    """Trigger a switchover.

    Args:
        ops_test: The ops test framework instance.
        current_primary: The current primary unit.
        candidate: The unit that should be elected the new primary.
    """
    primary_ip = get_unit_address(ops_test, current_primary)
    response = requests.post(
        f"http://{primary_ip}:8008/switchover",
        json={
            "leader": current_primary.replace("/", "-"),
            "candidate": candidate.replace("/", "-") if candidate else None,
        },
    )
    assert response.status_code == 200
