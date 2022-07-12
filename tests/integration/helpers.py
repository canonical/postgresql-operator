#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
from typing import List

import psycopg2
import yaml
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def build_application_name(series: str) -> str:
    """Return a composite application name combining application name and series."""
    return f"{APP_NAME}-{series}"


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    dict = {}
    for record in records:
        # Add record tuple data to dict.
        dict[record[0]] = record[1]
    return dict


def db_connect(host: str, password: str):
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host
        password: postgres password

    Returns:
        psycopg2 connection object linked to postgres db, under "postgres" user.
    """
    return psycopg2.connect(
        f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=10"
    )


async def get_postgres_password(ops_test: OpsTest, unit_name: str) -> str:
    """Retrieve the postgres user password using the action."""
    unit = ops_test.model.units.get(unit_name)
    action = await unit.run_action("get-initial-password")
    result = await action.wait()
    return result.results["postgres-password"]


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
