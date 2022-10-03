#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import json
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
    RetryError,
    Retrying,
    retry,
    retry_if_exception,
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
        timeout=2000,
    )

    return relation.id


def enable_connections_logging(ops_test: OpsTest, unit_name: str) -> None:
    """Turn on the log of all connections made to a PostgreSQL instance.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to turn on the connection logs
    """
    unit_address = get_unit_address(ops_test, unit_name)
    requests.patch(
        f"https://{unit_address}:8008/config",
        json={"postgresql": {"parameters": {"log_connections": True}}},
        verify=False,
    )


async def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = "postgres",
    sslmode: str = None,
):
    """Execute given PostgreSQL query on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the query on.
        password: The PostgreSQL superuser password.
        query: Query to execute.
        database: Optional database to connect to (defaults to postgres database).
        sslmode: Optional ssl mode to use (defaults to None).

    Returns:
        A list of rows that were potentially returned from the query.
    """
    extra_connection_parameters = f"sslmode={sslmode}" if sslmode else ""
    with psycopg2.connect(
        f"dbname='{database}' user='operator' host='{unit_address}'"
        f"password='{password}' connect_timeout=10 {extra_connection_parameters}"
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


@retry(
    retry=retry_if_exception(KeyError),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
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


async def get_tls_ca(
    ops_test: OpsTest,
    unit_name: str,
) -> str:
    """Returns the TLS CA used by the unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit

    Returns:
        TLS CA or an empty string if there is no CA.
    """
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [
        v for v in data[unit_name]["relation-info"] if v["endpoint"] == "certificates"
    ]
    if len(relation_data) == 0:
        return ""
    return json.loads(relation_data[0]["application-data"]["certificates"])[0].get("ca")


def get_unit_address(ops_test: OpsTest, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    return ops_test.model.units.get(unit_name).public_address


async def check_tls(ops_test: OpsTest, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on the specific PostgreSQL instance.

    Args:
        ops_test: The ops test framework instance.
        unit_name: The name of the unit of the PostgreSQL instance.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled.
    """
    unit_address = get_unit_address(ops_test, unit_name)
    password = await get_password(ops_test, unit_name)
    # Get the IP addresses of the other units to check that they
    # are connecting to the primary unit (if unit_name is the
    # primary unit name) using encrypted connections.
    app_name = unit_name.split("/")[0]
    unit_addresses = [
        f"'{get_unit_address(ops_test, other_unit_name)}'"
        for other_unit_name in ops_test.model.units
        if other_unit_name.split("/")[0] == app_name and other_unit_name != unit_name
    ]
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                output = await execute_query_on_unit(
                    unit_address,
                    password,
                    "SHOW ssl;",
                    sslmode="require" if enabled else "disable",
                )
                tls_enabled = "on" in output

                # Check for the number of bits in the encryption algorithm used
                # on each connection. If a connection is not encrypted, None
                # is returned instead of an integer.
                connections_encryption_info = await execute_query_on_unit(
                    unit_address,
                    password,
                    "SELECT bits FROM pg_stat_ssl INNER JOIN pg_stat_activity"
                    " ON pg_stat_ssl.pid = pg_stat_activity.pid"
                    " WHERE pg_stat_ssl.pid = pg_backend_pid()"
                    f" OR client_addr IN ({','.join(unit_addresses)});",
                )

                # This flag indicates whether all the connections are encrypted
                # when checking for TLS enabled or all the connections are not
                # encrypted when checking for TLS disabled.
                connections_encrypted = (
                    all(connections_encryption_info)
                    if enabled
                    else any(connections_encryption_info)
                )

                if enabled != tls_enabled or tls_enabled != connections_encrypted:
                    raise ValueError(
                        f"TLS is{' not' if not tls_enabled else ''} enabled on {unit_name}"
                    )
                return True
    except RetryError:
        return False


async def check_tls_patroni_api(ops_test: OpsTest, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on Patroni REST API.

    Args:
        ops_test: The ops test framework instance.
        unit_name: The name of the unit where Patroni is running.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled on Patroni REST API.
    """
    unit_address = get_unit_address(ops_test, unit_name)
    tls_ca = await get_tls_ca(ops_test, unit_name)

    # If there is no TLS CA in the relation, something is wrong in
    # the relation between the TLS Certificates Operator and PostgreSQL.
    if enabled and not tls_ca:
        return False

    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt, tempfile.NamedTemporaryFile() as temp_ca_file:
                # Write the TLS CA to a temporary file to use it in a request.
                temp_ca_file.write(tls_ca.encode("utf-8"))
                temp_ca_file.seek(0)

                # The CA bundle file is used to validate the server certificate when
                # TLS is enabled, otherwise True is set because it's the default value
                # for the verify parameter.
                health_info = requests.get(
                    f"{'https' if enabled else 'http'}://{unit_address}:8008/health",
                    verify=temp_ca_file.name if enabled else True,
                )
                return health_info.status_code == 200
    except RetryError:
        return False
    return False


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def primary_changed(ops_test: OpsTest, old_primary: str) -> bool:
    """Checks whether the primary unit has changed.

    Args:
        ops_test: The ops test framework instance
        old_primary: The name of the unit that was the primary before.
    """
    other_unit = [
        unit.name
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units
        if unit.name != old_primary
    ][0]
    primary = await get_primary(ops_test, other_unit)
    return primary != old_primary


async def run_command_on_unit(ops_test: OpsTest, unit_name: str, command: str) -> str:
    """Run a command on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to run the command on
        command: The command to run

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    complete_command = f"run --unit {unit_name} -- {command}"
    return_code, stdout, _ = await ops_test.juju(*complete_command.split())
    if return_code != 0:
        raise Exception(
            "Expected command %s to succeed instead it failed: %s", command, return_code
        )
    return stdout


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
