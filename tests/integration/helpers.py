#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List

import psycopg2
import requests
import yaml
from juju.unit import Unit
from pytest_operator.plugin import OpsTest
from tenacity import (
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]


async def build_connection_string(
    ops_test: OpsTest,
    application_name: str,
    relation_name: str,
    read_only_endpoint: bool = False,
) -> str:
    """Returns a PostgreSQL connection string.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        relation_name: name of the relation to get connection data from
        read_only_endpoint: whether to choose the read-only endpoint
            instead of the read/write endpoint

    Returns:
        a PostgreSQL connection string
    """
    unit_name = f"{application_name}/0"
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [v for v in data[unit_name]["relation-info"] if v["endpoint"] == relation_name]
    if len(relation_data) == 0:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name}"
        )
    data = relation_data[0]["application-data"]
    if read_only_endpoint:
        return data.get("standbys").split(",")[0]
    else:
        return data.get("master")


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
    unit_address = await unit.get_public_address()
    password = await get_password(ops_test, unit.name)

    # Retrieve all users in the database.
    users_in_db = await execute_query_on_unit(
        unit_address,
        password,
        "SELECT usename FROM pg_catalog.pg_user;",
    )

    # Assert users that should exist.
    for user in users_that_should_exist:
        assert user in users_in_db

    # Assert users that should not exist.
    for user in users_that_should_not_exist:
        assert user not in users_in_db


async def check_databases_creation(ops_test: OpsTest, databases: List[str]) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        databases: List of database names that should have been created
    """
    unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
    password = await get_password(ops_test, unit.name)

    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        unit_address = await unit.get_public_address()

        for database in databases:
            # Ensure database exists in PostgreSQL.
            output = await execute_query_on_unit(
                unit_address,
                password,
                "SELECT datname FROM pg_database;",
            )
            assert database in output

            # Ensure that application tables exist in the database
            output = await execute_query_on_unit(
                unit_address,
                password,
                "SELECT table_name FROM information_schema.tables;",
                database=database,
            )
            assert len(output)


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
def check_patroni(ops_test: OpsTest, unit_name: str, restart_time: float) -> bool:
    """Check if Patroni is running correctly on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
        restart_time: Point in time before the unit was restarted.

    Returns:
        whether Patroni is running correctly.
    """
    unit_ip = get_unit_address(ops_test, unit_name)
    health_info = requests.get(f"http://{unit_ip}:8008/health").json()
    postmaster_start_time = datetime.strptime(
        health_info["postmaster_start_time"], "%Y-%m-%d %H:%M:%S.%f%z"
    ).timestamp()
    return postmaster_start_time > restart_time and health_info["state"] == "running"


def build_application_name(series: str) -> str:
    """Return a composite application name combining application name and series."""
    return f"{DATABASE_APP_NAME}-{series}"


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
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


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


async def deploy_and_relate_application_with_postgresql(
    ops_test: OpsTest,
    charm: str,
    application_name: str,
    number_of_units: int,
    config: dict = None,
    channel: str = "stable",
    relation: str = "db",
) -> int:
    """Helper function to deploy and relate application with PostgreSQL.

    Args:
        ops_test: The ops test framework.
        charm: Charm identifier.
        application_name: The name of the application to deploy.
        number_of_units: The number of units to deploy.
        config: Extra config options for the application.
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
        config=config,
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
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


async def deploy_and_relate_bundle_with_postgresql(
    ops_test: OpsTest,
    bundle: str,
    application_name: str,
) -> str:
    """Helper function to deploy and relate a bundle with PostgreSQL.

    Args:
        ops_test: The ops test framework.
        bundle: Bundle identifier.
        application_name: The name of the application to check for
            an active state after the deployment.
    """
    # Deploy the bundle.
    with tempfile.NamedTemporaryFile() as original:
        # Download the original bundle.
        await ops_test.juju("download", bundle, "--filepath", original.name)

        # Open the bundle compressed file and update the contents
        # of the bundle.yaml file to deploy it.
        with zipfile.ZipFile(original.name, "r") as archive:
            bundle_yaml = archive.read("bundle.yaml")
            data = yaml.load(bundle_yaml, Loader=yaml.FullLoader)

            # Remove PostgreSQL and relations with it from the bundle.yaml file.
            del data["services"]["postgresql"]
            data["relations"] = [
                relation
                for relation in data["relations"]
                if "postgresql:db" not in relation and "postgresql:db-admin" not in relation
            ]

            # Write the new bundle content to a temporary file and deploy it.
            with tempfile.NamedTemporaryFile() as patched:
                patched.write(yaml.dump(data).encode("utf_8"))
                patched.seek(0)
                await ops_test.juju("deploy", patched.name)

    # Relate application to PostgreSQL.
    relation = await ops_test.model.relate(f"{application_name}", f"{DATABASE_APP_NAME}:db-admin")
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
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
        f"dbname='{database}' user='operator' host='{unit_address}' password='{password}' connect_timeout=10"
    ) as connection, connection.cursor() as cursor:
        cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))
    return output


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


async def get_password(ops_test: OpsTest, unit_name: str, username: str = "operator") -> str:
    """Retrieve a user password using the action.

    Args:
        ops_test: ops_test instance.
        unit_name: the name of the unit.
        username: the user to get the password.

    Returns:
        the user password.
    """
    unit = ops_test.model.units.get(unit_name)
    action = await unit.run_action("get-password", **{"username": username})
    result = await action.wait()
    return result.results[f"{username}-password"]


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


def restart_patroni(ops_test: OpsTest, unit_name: str) -> None:
    """Restart Patroni on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
    """
    unit_ip = get_unit_address(ops_test, unit_name)
    requests.post(f"http://{unit_ip}:8008/restart")


async def set_password(
    ops_test: OpsTest, unit_name: str, username: str = "operator", password: str = None
):
    """Set a user password using the action.

    Args:
        ops_test: ops_test instance.
        unit_name: the name of the unit.
        username: the user to set the password.
        password: optional password to use
            instead of auto-generating

    Returns:
        the results from the action.
    """
    unit = ops_test.model.units.get(unit_name)
    parameters = {"username": username}
    if password is not None:
        parameters["password"] = password
    action = await unit.run_action("set-password", **parameters)
    result = await action.wait()
    return result.results


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
