#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
from pathlib import Path
from typing import List

import psycopg2
import requests
import yaml
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]
TLS_RESOURCES = {
    "cert-file": "tests/tls/server.crt",
    "key-file": "tests/tls/server.key",
}


async def attach_resource(ops_test: OpsTest, app_name: str, rsc_name: str, rsc_path: str) -> None:
    """Use the `juju attach-resource` command to add resources."""
    # logger.info(f"Attaching resource: attach-resource {APP_NAME} {rsc_name}={rsc_path}")
    await ops_test.juju("attach-resource", app_name, f"{rsc_name}={rsc_path}")


async def check_database_users_existence(
    ops_test: OpsTest,
    users_that_should_exist: List[str],
    users_that_should_not_exist: List[str],
) -> None:
    """Checks that applications users exist in the database.

    Args:
        ops_test: The ops test framework
        users_that_should_exist: List of users that should exist in the database
        users_that_should_not_exist: List of users that should not exist in the database
    """
    unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
    unit_address = await get_unit_address(ops_test, unit.name)
    password = await get_postgres_password(ops_test)

    # Retrieve all users in the database.
    output = await execute_query_on_unit(
        unit_address,
        password,
        "SELECT usename FROM pg_catalog.pg_user;",
    )
    print(f"output: {output}")

    # Assert users that should exist.
    for user in users_that_should_exist:
        assert user in output

    # Assert users that should not exist.
    for user in users_that_should_not_exist:
        assert user not in output


async def check_database_creation(ops_test: OpsTest, database: str) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        database: Name of the database that should have been created
    """
    password = await get_postgres_password(ops_test)

    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        unit_address = await get_unit_address(ops_test, unit.name)

        # Ensure database exists in PostgreSQL.
        output = await execute_query_on_unit(
            unit_address,
            password,
            "SELECT datname FROM pg_database;",
        )
        print(f"output: {output}")
        assert database in output

        # Ensure that application tables exist in the database
        output = await execute_query_on_unit(
            unit_address,
            password,
            "SELECT table_name FROM information_schema.tables;",
            database=database,
        )
        print(f"output: {output}")
        assert len(output)


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


async def deploy_and_relate_application_with_postgresql(
    ops_test: OpsTest,
    charm: str,
    application_name: str,
    number_of_units: int,
    channel: str = "stable",
    relation: str = "db",
) -> int:
    """Helper function to deploy and relate application with PostgreSQL.

    Args:
        ops_test: The ops test framework.
        charm: Charm identifier.
        application_name: The name of the application to deploy.
        number_of_units: The number of units to deploy.
        channel: The channel to use for the charm.
        relation: Name of the PostgreSQL relation to relate
            the application to.

    Returns:
        the id of the created relation.
    """
    # Deploy application.
    await ops_test.model.deploy(
        charm,
        channel=channel,
        application_name=application_name,
        num_units=number_of_units,
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="blocked",
        raise_on_blocked=False,
        timeout=1000,
    )

    # Relate application to PostgreSQL.
    relation = await ops_test.model.relate(
        f"{application_name}", f"{DATABASE_APP_NAME}:{relation}"
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
        raise_on_blocked=False,  # Application that needs a relation is blocked initially.
        timeout=1000,
    )

    return relation.id


async def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = "postgres",
):
    """Execute given PostgreSQL query on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the query on.
        password: The PostgreSQL superuser password.
        query: Query to execute.
        database: Optional database to connect to (defaults to postgres database).

    Returns:
        A list of rows that were potentially returned from the query.
    """
    with psycopg2.connect(
        f"dbname='{database}' user='postgres' host='{unit_address}' password='{password}' connect_timeout=10"
    ) as connection, connection.cursor() as cursor:
        cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))
    return output


def get_cluster_members(endpoint: str) -> List[str]:
    """List of current Patroni cluster members.

    Args:
        endpoint: endpoint of the Patroni API

    Returns:
        list of Patroni cluster members
    """
    r = requests.get(f"http://{endpoint}:8008/cluster")
    return [member["name"] for member in r.json()["members"]]


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


async def get_postgres_password(ops_test: OpsTest):
    """Retrieve the postgres user password using the action."""
    unit = ops_test.model.units.get(f"{DATABASE_APP_NAME}/0")
    action = await unit.run_action("get-postgres-password")
    result = await action.wait()
    return result.results["postgres-password"]


async def get_primary(ops_test: OpsTest, unit_id=0) -> str:
    """Get the primary unit.

    Args:
        ops_test: ops_test instance.
        unit_id: the number of the unit.

    Returns:
        the current primary unit.
    """
    action = await ops_test.model.units.get(f"{DATABASE_APP_NAME}/{unit_id}").run_action(
        "get-primary"
    )
    action = await action.wait()
    return action.results["primary"]


async def get_unit_address(ops_test: OpsTest, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    status = await ops_test.model.get_status()
    return status["applications"][unit_name.split("/")[0]].units[unit_name]["address"]


async def scale_application(ops_test: OpsTest, application_name: str, scale: int) -> None:
    """Scale a given application to a specific unit count.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        scale: The number of units to scale to
    """
    await ops_test.model.applications[application_name].scale(scale)
    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)
