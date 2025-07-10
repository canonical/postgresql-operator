#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import itertools
import json
import logging
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import botocore
import psycopg2
import pytest
import requests
import yaml
from juju.model import Model
from juju.unit import Unit
from pytest_operator.plugin import OpsTest
from tenacity import (
    RetryError,
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

from constants import DATABASE_DEFAULT_NAME, PEER, SYSTEM_USERS_PASSWORD_CONFIG

CHARM_BASE = "ubuntu@22.04"
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]
STORAGE_PATH = METADATA["storage"]["data"]["location"]
APPLICATION_NAME = "postgresql-test-app"
DATA_INTEGRATOR_APP_NAME = "data-integrator"


class SecretNotFoundError(Exception):
    """Raised when a secret is not found."""


logger = logging.getLogger(__name__)


async def build_connection_string(
    ops_test: OpsTest,
    application_name: str,
    relation_name: str,
    read_only_endpoint: bool = False,
    remote_unit_name: str | None = None,
) -> str | None:
    """Returns a PostgreSQL connection string.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        relation_name: name of the relation to get connection data from
        read_only_endpoint: whether to choose the read-only endpoint
            instead of the read/write endpoint
        remote_unit_name: Optional remote unit name used to retrieve
            unit data instead of application data

    Returns:
        a PostgreSQL connection string
    """
    unit_name = f"{application_name}/0"
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [
        v for v in data[unit_name]["relation-info"] if v["related-endpoint"] == relation_name
    ]
    if len(relation_data) == 0:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name}"
        )
    if remote_unit_name:
        data = relation_data[0]["related-units"][remote_unit_name]["data"]
    else:
        data = relation_data[0]["application-data"]
    if read_only_endpoint:
        if data.get("standbys") is None:
            return None
        return data.get("standbys").split(",")[0]
    else:
        return data.get("master")


def change_primary_start_timeout(
    ops_test: OpsTest, unit_name: str, seconds: int | None, password: str
) -> None:
    """Change primary start timeout configuration.

    Args:
        ops_test: ops_test instance.
        unit_name: the unit used to set the configuration.
        seconds: number of seconds to set in primary_start_timeout configuration.
        password: Patroni password.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            unit_ip = get_unit_address(ops_test, unit_name)
            requests.patch(
                f"https://{unit_ip}:8008/config",
                json={"primary_start_timeout": seconds},
                verify=False,
                auth=requests.auth.HTTPBasicAuth("patroni", password),
            )


def get_patroni_cluster(unit_ip: str) -> dict[str, str]:
    resp = requests.get(f"https://{unit_ip}:8008/cluster", verify=False)
    return resp.json()


def assert_sync_standbys(unit_ip: str, standbys: int) -> None:
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
        with attempt:
            cluster = get_patroni_cluster(unit_ip)
            cluster_standbys = 0
            for member in cluster["members"]:
                if member["role"] == "sync_standby":
                    cluster_standbys += 1
            assert cluster_standbys >= standbys, "Less than expected standbys"


async def check_database_users_existence(
    ops_test: OpsTest,
    users_that_should_exist: list[str],
    users_that_should_not_exist: list[str],
) -> None:
    """Checks that applications users exist in the database.

    Args:
        ops_test: The ops test framework
        users_that_should_exist: List of users that should exist in the database
        users_that_should_not_exist: List of users that should not exist in the database
    """
    unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
    unit_address = await unit.get_public_address()
    password = await get_password(ops_test)

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


async def check_databases_creation(ops_test: OpsTest, databases: list[str]) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        databases: List of database names that should have been created
    """
    password = await get_password(ops_test)

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
    health_info = requests.get(f"https://{unit_ip}:8008/health", verify=False).json()
    postmaster_start_time = datetime.strptime(
        health_info["postmaster_start_time"], "%Y-%m-%d %H:%M:%S.%f%z"
    ).timestamp()
    return postmaster_start_time > restart_time and health_info["state"] == "running"


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

            r = requests.get(f"https://{address}:8008/cluster", verify=False)
            assert [member["name"] for member in r.json()["members"]] == expected_members
            assert [member["host"] for member in r.json()["members"]] == expected_members_ips


def construct_endpoint(endpoint: str, region: str) -> str:
    """Construct the S3 service endpoint using the region.

    This is needed when the provided endpoint is from AWS, and it doesn't contain the region.
    """
    # Load endpoints data.
    loader = botocore.loaders.create_loader()
    data = loader.load_data("endpoints")

    # Construct the endpoint using the region.
    resolver = botocore.regions.EndpointResolver(data)
    endpoint_data = resolver.construct_endpoint("s3", region)

    # Use the built endpoint if it is an AWS endpoint.
    if endpoint_data and endpoint.endswith(endpoint_data["dnsSuffix"]):
        endpoint = f"{endpoint.split('://')[0]}://{endpoint_data['hostname']}"

    return endpoint


def convert_records_to_dict(records: list[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


def count_switchovers(ops_test: OpsTest, unit_name: str) -> int:
    """Return the number of performed switchovers."""
    unit_address = get_unit_address(ops_test, unit_name)
    switchover_history_info = requests.get(f"https://{unit_address}:8008/history", verify=False)
    return len(switchover_history_info.json())


def db_connect(
    host: str, password: str, username: str = "operator", database: str = "postgres"
) -> psycopg2.extensions.connection:
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host
        password: user password
        username: username to connect with
        database: database to connect to

    Returns:
        psycopg2 connection object linked to postgres db, under "operator" user.
    """
    return psycopg2.connect(
        f"dbname='{database}' user='{username}' host='{host}' password='{password}' connect_timeout=10"
    )


async def deploy_and_relate_application_with_postgresql(
    ops_test: OpsTest,
    charm: str,
    application_name: str,
    number_of_units: int,
    config: dict | None = None,
    channel: str = "stable",
    relation: str = "db",
    series: str | None = None,
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
        series: Series of the charm to deploy.

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
        series=series,
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    # Relate application to PostgreSQL.
    relation = await ops_test.model.relate(
        f"{application_name}", f"{DATABASE_APP_NAME}:{relation}"
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
        raise_on_blocked=False,  # Application that needs a relation is blocked initially.
        timeout=1500,
    )

    return relation.id


async def deploy_and_relate_bundle_with_postgresql(
    ops_test: OpsTest,
    bundle_name: str,
    main_application_name: str,
    main_application_num_units: int | None = None,
    relation_name: str = "db",
    status: str = "active",
    status_message: str | None = None,
    overlay: dict | None = None,
    timeout: int = 2000,
) -> str:
    """Helper function to deploy and relate a bundle with PostgreSQL.

    Args:
        ops_test: The ops test framework.
        bundle_name: The name of the bundle to deploy.
        main_application_name: The name of the application that should be
            related to PostgreSQL.
        main_application_num_units: Optional number of units for the main
            application.
        relation_name: The name of the relation to use in PostgreSQL
            (db or db-admin).
        status: Status to wait for in the application after relating
            it to PostgreSQL.
        status_message: Status message to wait for in the application after
            relating it to PostgreSQL.
        overlay: Optional overlay to be used when deploying the bundle.
        timeout: Timeout to wait for the deployment to idle.
    """
    # Deploy the bundle.
    with tempfile.NamedTemporaryFile(dir=os.getcwd()) as original:
        # Download the original bundle.
        await ops_test.juju("download", bundle_name, "--filepath", original.name)

        # Open the bundle compressed file and update the contents
        # of the bundle.yaml file to deploy it.
        with zipfile.ZipFile(original.name, "r") as archive:
            bundle_yaml = archive.read("bundle.yaml")
            data = yaml.load(bundle_yaml, Loader=yaml.FullLoader)

            if main_application_num_units is not None:
                data["applications"][main_application_name]["num_units"] = (
                    main_application_num_units
                )

            # Save the list of relations other than `db` and `db-admin`,
            # so we can add them back later.
            other_relations = [
                relation for relation in data["relations"] if "postgresql" in relation
            ]

            # Remove PostgreSQL and relations with it from the bundle.yaml file.
            config = data["applications"]["postgresql"]["options"]
            if config.get("experimental_max_connections", 0) > 200:
                config["experimental_max_connections"] = 200
            for key, val in config.items():
                config[key] = str(val)
            logger.info(f"Bundle {bundle_name} needs configuration {config}")
            await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
            del data["applications"]["postgresql"]
            data["relations"] = [
                relation
                for relation in data["relations"]
                if "postgresql" not in relation
                and "postgresql:db" not in relation
                and "postgresql:db-admin" not in relation
            ]

            # Write the new bundle content to a temporary file and deploy it.
            with tempfile.NamedTemporaryFile(dir=os.getcwd()) as patched:
                patched.write(yaml.dump(data).encode("utf_8"))
                patched.seek(0)
                if overlay is not None:
                    with tempfile.NamedTemporaryFile() as overlay_file:
                        overlay_file.write(yaml.dump(overlay).encode("utf_8"))
                        overlay_file.seek(0)
                        await ops_test.juju("deploy", patched.name, "--overlay", overlay_file.name)
                else:
                    await ops_test.juju("deploy", patched.name)

    async with ops_test.fast_forward(fast_interval="60s"):
        # Relate application to PostgreSQL.
        relation = await ops_test.model.relate(
            main_application_name, f"{DATABASE_APP_NAME}:{relation_name}"
        )

        # Restore previous existing relations.
        for other_relation in other_relations:
            await ops_test.model.relate(other_relation[0], other_relation[1])

        # Wait for the deployment to complete.
        unit = ops_test.model.units.get(f"{main_application_name}/0")
        awaits = [
            ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME],
                status="active",
                timeout=timeout,
            ),
            ops_test.model.wait_for_idle(
                apps=[main_application_name],
                raise_on_blocked=False,
                status=status,
                timeout=timeout,
            ),
        ]
        if status_message:
            awaits.append(
                ops_test.model.block_until(
                    lambda: unit.workload_status_message == status_message,
                    timeout=timeout,
                )
            )
        await asyncio.gather(*awaits)

    return relation.id


async def ensure_correct_relation_data(
    ops_test: OpsTest, database_units: int, app_name: str, relation_name: str
) -> None:
    """Asserts that the correct database relation data is shared from the right unit to the app."""
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    for unit_number in range(database_units):
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                unit_name = f"{DATABASE_APP_NAME}/{unit_number}"
                primary_connection_string = await build_connection_string(
                    ops_test, app_name, relation_name, remote_unit_name=unit_name
                )
                replica_connection_string = await build_connection_string(
                    ops_test,
                    app_name,
                    relation_name,
                    read_only_endpoint=True,
                    remote_unit_name=unit_name,
                )
                unit_ip = get_unit_address(ops_test, unit_name)
                host_parameter = f"host={unit_ip} "
                if unit_name == primary:
                    logger.info(f"Expected primary: {unit_ip}")
                    logger.info(f"Primary conn string: {primary_connection_string}")
                    logger.info(f"Replica conn string: {replica_connection_string}")
                    assert host_parameter in primary_connection_string, (
                        f"{unit_name} is not the host of the primary connection string"
                    )
                    assert host_parameter not in replica_connection_string, (
                        f"{unit_name} is the host of the replica connection string"
                    )


async def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = DATABASE_DEFAULT_NAME,
    sslmode: str | None = None,
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
    with (
        psycopg2.connect(
            f"dbname='{database}' user='operator' host='{unit_address}'"
            f"password='{password}' connect_timeout=10 {extra_connection_parameters}"
        ) as connection,
        connection.cursor() as cursor,
    ):
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


def get_application_units(ops_test: OpsTest, application_name: str) -> list[str]:
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


def get_application_units_ips(ops_test: OpsTest, application_name: str) -> list[str]:
    """List the unit IPs of an application.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application

    Returns:
        list of current unit IPs of the application
    """
    return [unit.public_address for unit in ops_test.model.applications[application_name].units]


async def get_landscape_api_credentials(ops_test: OpsTest) -> list[str]:
    """Returns the key and secret to be used in the Landscape API.

    Args:
        ops_test: The ops test framework
    """
    unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
    password = await get_password(ops_test)
    unit_address = await unit.get_public_address()

    output = await execute_query_on_unit(
        unit_address,
        password,
        "SELECT encode(access_key_id,'escape'), encode(access_secret_key,'escape') FROM api_credentials;",
        database="landscape-standalone-main",
    )

    return output


async def get_leader_unit(ops_test: OpsTest, app: str, model: Model = None) -> Unit | None:
    if model is None:
        model = ops_test.model

    leader_unit = None
    for unit in model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
            break

    return leader_unit


async def get_machine_from_unit(ops_test: OpsTest, unit_name: str) -> str:
    """Get the name of the machine from a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to get the machine

    Returns:
        The name of the machine.
    """
    raw_hostname = await run_command_on_unit(ops_test, unit_name, "hostname")
    return raw_hostname.strip()


async def get_password(
    ops_test: OpsTest,
    username: str = "operator",
    database_app_name: str = DATABASE_APP_NAME,
) -> str:
    """Retrieve a user password from the secret.

    Args:
        ops_test: ops_test instance.
        username: the user to get the password.
        database_app_name: the app for getting the secret

    Returns:
        the user password.
    """
    secret = await get_secret_by_label(ops_test, label=f"{PEER}.{database_app_name}.app")
    password = secret.get(f"{username}-password")

    return password


async def get_secret_by_label(ops_test: OpsTest, label: str) -> dict[str, str]:
    secrets_raw = await ops_test.juju("list-secrets")
    secret_ids = [
        secret_line.split()[0] for secret_line in secrets_raw[1].split("\n")[1:] if secret_line
    ]

    for secret_id in secret_ids:
        secret_data_raw = await ops_test.juju(
            "show-secret", "--format", "json", "--reveal", secret_id
        )
        secret_data = json.loads(secret_data_raw[1])

        if label == secret_data[secret_id].get("label"):
            return secret_data[secret_id]["content"]["Data"]

    raise SecretNotFoundError(f"Secret with label {label} not found")


@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def get_primary(ops_test: OpsTest, unit_name: str, model=None) -> str:
    """Get the primary unit.

    Args:
        ops_test: ops_test instance.
        unit_name: the name of the unit.
        model: Model to use.

    Returns:
        the current primary unit.
    """
    if not model:
        model = ops_test.model
    action = await model.units.get(unit_name).run_action("get-primary")
    action = await action.wait()
    if "primary" not in action.results or action.results["primary"] not in model.units:
        raise Exception("Primary unit not found")
    return action.results["primary"]


async def get_tls_ca(ops_test: OpsTest, unit_name: str, relation: str = "client") -> str:
    """Returns the TLS CA used by the unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
        relation: TLS relation to get the CA from

    Returns:
        TLS CA or an empty string if there is no CA.
    """
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]
    endpoint = f"{relation}-certificates"
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [v for v in data[unit_name]["relation-info"] if v["endpoint"] == endpoint]
    if len(relation_data) == 0:
        return ""
    return json.loads(relation_data[0]["application-data"]["certificates"])[0].get("ca")


@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def get_unit_address(ops_test: OpsTest, unit_name: str, model: Model = None) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
        model: Optional model to use to get the unit address

    Returns:
        IP address of the unit
    """
    if model is None:
        model = ops_test.model
    return model.units.get(unit_name).public_address


def check_connected_user(
    cursor, session_user: str, current_user: str, primary: bool = True
) -> None:
    cursor.execute("SELECT session_user,current_user;")
    result = cursor.fetchone()
    if result is not None:
        instance = "primary" if primary else "replica"
        assert result[0] == session_user, (
            f"The session user should be the {session_user} user in the {instance} (it's currently {result[0]})"
        )
        assert result[1] == current_user, (
            f"The current user should be the {current_user} user in the {instance} (it's currently {result[1]})"
        )
    else:
        assert False, "No result returned from the query"


async def check_roles_and_their_permissions(
    ops_test: OpsTest, relation_endpoint: str, database_name: str
) -> None:
    action = await ops_test.model.units[f"{DATA_INTEGRATOR_APP_NAME}/0"].run_action(
        action_name="get-credentials"
    )
    result = await action.wait()
    data_integrator_credentials = result.results
    username = data_integrator_credentials[relation_endpoint]["username"]
    uris = data_integrator_credentials[relation_endpoint]["uris"]
    connection = None
    try:
        connection = psycopg2.connect(uris)
        connection.autocommit = True
        with connection.cursor() as cursor:
            logger.info(
                "Checking that the relation user is automatically escalated to the database owner user"
            )
            check_connected_user(cursor, username, f"{database_name}_owner")
            logger.info("Creating a test table and inserting data")
            cursor.execute("CREATE TABLE test_table (id INTEGER);")
            logger.info("Inserting data into the test table")
            cursor.execute("INSERT INTO test_table(id) VALUES(1);")
            logger.info("Reading data from the test table")
            cursor.execute("SELECT * FROM test_table;")
            result = cursor.fetchall()
            assert len(result) == 1, "The database owner user should be able to read the data"

            logger.info("Checking that the database owner user can't create a database")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(f"CREATE DATABASE {database_name}_2;")

            logger.info("Checking that the relation user can't create a table")
            cursor.execute("RESET ROLE;")
            check_connected_user(cursor, username, username)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("CREATE TABLE test_table_2 (id INTEGER);")

            logger.info(
                "Checking that the relation user can escalate back to the database owner user"
            )
            cursor.execute(f"SET ROLE {database_name}_owner;")
            check_connected_user(cursor, username, f"{database_name}_owner")
    finally:
        if connection is not None:
            connection.close()

    connection_string = f"host={data_integrator_credentials[relation_endpoint]['read-only-endpoints'].split(':')[0]} dbname={data_integrator_credentials[relation_endpoint]['database']} user={username} password={data_integrator_credentials[relation_endpoint]['password']}"
    connection = None
    try:
        connection = psycopg2.connect(connection_string)
        with connection.cursor() as cursor:
            logger.info("Checking that the relation user can read data from the database")
            check_connected_user(cursor, username, username, primary=False)
            logger.info("Reading data from the test table")
            cursor.execute("SELECT * FROM test_table;")
            result = cursor.fetchall()
            assert len(result) == 1, "The relation user should be able to read the data"
    finally:
        if connection is not None:
            connection.close()


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
    password = await get_password(ops_test)
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


async def check_tls_replication(ops_test: OpsTest, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on the replica PostgreSQL instance.

    Args:
        ops_test: The ops test framework instance.
        unit_name: The name of the replica of the PostgreSQL instance.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled.
    """
    unit_address = get_unit_address(ops_test, unit_name)
    password = await get_password(ops_test)

    # Check for the all replicas using encrypted connection
    output = await execute_query_on_unit(
        unit_address,
        password,
        "SELECT pg_ssl.ssl, pg_sa.client_addr FROM pg_stat_ssl pg_ssl"
        " JOIN pg_stat_activity pg_sa ON pg_ssl.pid = pg_sa.pid"
        " AND pg_sa.usename = 'replication';",
    )
    return all(output[i] == enabled for i in range(0, len(output), 2))


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
    tls_ca = await get_tls_ca(ops_test, unit_name, "peer")

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
                # peer TLS is enabled, otherwise don't validate the internal cert.
                health_info = requests.get(
                    f"https://{unit_address}:8008/health",
                    verify=temp_ca_file.name if enabled else False,
                )
                return health_info.status_code == 200
    except RetryError:
        return False
    return False


def has_relation_exited(
    ops_test: OpsTest, endpoint_one: str, endpoint_two: str, model: Model = None
) -> bool:
    """Returns true if the relation between endpoint_one and endpoint_two has been removed."""
    relations = model.relations if model is not None else ops_test.model.relations
    for rel in relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint_one in endpoints and endpoint_two in endpoints:
            return False
    return True


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
    other_unit = next(
        unit.name
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units
        if unit.name != old_primary
    )
    primary = await get_primary(ops_test, other_unit)
    return primary != old_primary


def relations(ops_test: OpsTest, provider_app: str, requirer_app: str) -> list:
    return [
        relation
        for relation in ops_test.model.applications[provider_app].relations
        if not relation.is_peer and relation.requires.application_name == requirer_app
    ]


async def restart_machine(ops_test: OpsTest, unit_name: str) -> None:
    """Restart the machine where a unit run on.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to restart the machine
    """
    raw_hostname = await get_machine_from_unit(ops_test, unit_name)
    restart_machine_command = f"lxc restart {raw_hostname}"
    subprocess.check_call(restart_machine_command.split())


async def run_command_on_unit(ops_test: OpsTest, unit_name: str, command: str) -> str:
    """Run a command on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to run the command on
        command: The command to run

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    complete_command = ["exec", "--unit", unit_name, "--", *command.split()]
    return_code, stdout, _ = await ops_test.juju(*complete_command)
    if return_code != 0:
        logger.error(stdout)
        raise Exception(
            f"Expected command '{command}' to succeed instead it failed: {return_code}"
        )
    return stdout


async def scale_application(
    ops_test: OpsTest,
    application_name: str,
    count: int,
    model: Model = None,
    timeout=2000,
    idle_period: int = 30,
) -> None:
    """Scale a given application to a specific unit count.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        count: The desired number of units to scale to
        model: The model to scale the application in
        timeout: timeout period
        idle_period: idle period
    """
    if model is None:
        model = ops_test.model
    change = count - len(model.applications[application_name].units)
    if change > 0:
        await model.applications[application_name].add_units(change)
    elif change < 0:
        units = [unit.name for unit in model.applications[application_name].units[0:-change]]
        await model.applications[application_name].destroy_units(*units)
    await model.wait_for_idle(
        apps=[application_name],
        status="active",
        timeout=timeout,
        idle_period=idle_period,
        wait_for_exact_units=count,
    )


def restart_patroni(ops_test: OpsTest, unit_name: str, password: str) -> None:
    """Restart Patroni on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
        password: patroni password
    """
    unit_ip = get_unit_address(ops_test, unit_name)
    requests.post(
        f"https://{unit_ip}:8008/restart",
        auth=requests.auth.HTTPBasicAuth("patroni", password),
        verify=False,
    )


async def set_password(
    ops_test: OpsTest,
    username: str = "operator",
    password: str | None = None,
    database_app_name: str = DATABASE_APP_NAME,
):
    """Set a user password via secret.

    Args:
        ops_test: ops_test instance.
        username: the user to set the password.
        password: optional password to use
            instead of auto-generating
        database_app_name: name of the app for the secret

    Returns:
        the results from the action.
    """
    secret_name = "system_users_secret"

    try:
        secret_id = await ops_test.model.add_secret(
            name=secret_name, data_args=[f"{username}={password}"]
        )
        await ops_test.model.grant_secret(secret_name=secret_name, application=database_app_name)

        # update the application config to include the secret
        await ops_test.model.applications[database_app_name].set_config({
            SYSTEM_USERS_PASSWORD_CONFIG: secret_id
        })
    except Exception:
        await ops_test.model.update_secret(
            name=secret_name, data_args=[f"{username}={password}"], new_name=secret_name
        )


async def start_machine(ops_test: OpsTest, machine_name: str) -> None:
    """Start the machine where a unit run on.

    Args:
        ops_test: The ops test framework instance
        machine_name: The name of the machine to start
    """
    start_machine_command = f"lxc start {machine_name}"
    subprocess.check_call(start_machine_command.split())


async def stop_machine(ops_test: OpsTest, machine_name: str) -> None:
    """Stop the machine where a unit run on.

    Args:
        ops_test: The ops test framework instance
        machine_name: The name of the machine to stop
    """
    stop_machine_command = f"lxc stop {machine_name}"
    subprocess.check_call(stop_machine_command.split())


def switchover(
    ops_test: OpsTest, current_primary: str, password: str, candidate: str | None = None
) -> None:
    """Trigger a switchover.

    Args:
        ops_test: The ops test framework instance.
        current_primary: The current primary unit.
        password: Patroni password.
        candidate: The unit that should be elected the new primary.
    """
    primary_ip = get_unit_address(ops_test, current_primary)
    response = requests.post(
        f"https://{primary_ip}:8008/switchover",
        json={
            "leader": current_primary.replace("/", "-"),
            "candidate": candidate.replace("/", "-") if candidate else None,
        },
        auth=requests.auth.HTTPBasicAuth("patroni", password),
        verify=False,
    )
    assert response.status_code == 200
    app_name = current_primary.split("/")[0]
    for attempt in Retrying(stop=stop_after_attempt(30), wait=wait_fixed(2), reraise=True):
        with attempt:
            response = requests.get(f"https://{primary_ip}:8008/cluster", verify=False)
            assert response.status_code == 200
            standbys = len([
                member for member in response.json()["members"] if member["role"] == "sync_standby"
            ])
            assert standbys == len(ops_test.model.applications[app_name].units) - 1


async def wait_for_idle_on_blocked(
    ops_test: OpsTest,
    database_app_name: str,
    unit_number: int,
    other_app_name: str,
    status_message: str,
):
    """Wait for specific applications becoming idle and blocked together."""
    unit = ops_test.model.units.get(f"{database_app_name}/{unit_number}")
    await asyncio.gather(
        ops_test.model.wait_for_idle(apps=[other_app_name], status="active"),
        ops_test.model.block_until(
            lambda: unit.workload_status == "blocked"
            and unit.workload_status_message == status_message
        ),
    )


def wait_for_relation_removed_between(
    ops_test: OpsTest, endpoint_one: str, endpoint_two: str, model: Model = None
) -> None:
    """Wait for relation to be removed before checking if it's waiting or idle.

    Args:
        ops_test: running OpsTest instance
        endpoint_one: one endpoint of the relation. Doesn't matter if it's provider or requirer.
        endpoint_two: the other endpoint of the relation.
        model: optional model to check for the relation.
    """
    try:
        for attempt in Retrying(stop=stop_after_delay(3 * 60), wait=wait_fixed(3)):
            with attempt:
                if has_relation_exited(ops_test, endpoint_one, endpoint_two, model):
                    break
    except RetryError:
        assert False, "Relation failed to exit after 3 minutes."


async def backup_operations(
    ops_test: OpsTest,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str,
    tls_config,
    tls_channel,
    credentials,
    cloud,
    config,
    charm,
) -> None:
    """Basic set of operations for backup testing in different cloud providers."""
    # Deploy S3 Integrator and TLS Certificates Operator.
    await ops_test.model.deploy(s3_integrator_app_name)
    await ops_test.model.deploy(tls_certificates_app_name, config=tls_config, channel=tls_channel)

    # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
    # as archive_mode is disabled after restoring the backup) and to TLS Certificates Operator
    # (to be able to create backups from replicas).
    database_app_name = f"{DATABASE_APP_NAME}-{cloud.lower()}"
    await ops_test.model.deploy(
        charm,
        application_name=database_app_name,
        num_units=2,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )

    await ops_test.model.relate(
        f"{database_app_name}:client-certificates", f"{tls_certificates_app_name}:certificates"
    )
    await ops_test.model.relate(
        f"{database_app_name}:peer-certificates", f"{tls_certificates_app_name}:certificates"
    )
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(apps=[database_app_name], status="active", timeout=1000)

    # Configure and set access and secret keys.
    logger.info(f"configuring S3 integrator for {cloud}")
    await ops_test.model.applications[s3_integrator_app_name].set_config(config)
    action = await ops_test.model.units.get(f"{s3_integrator_app_name}/0").run_action(
        "sync-s3-credentials",
        **credentials,
    )
    await action.wait()

    await ops_test.model.relate(database_app_name, s3_integrator_app_name)
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, s3_integrator_app_name], status="active", timeout=1500
        )

    primary = await get_primary(ops_test, f"{database_app_name}/0")
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != primary:
            replica = unit.name
            break

    # Write some data.
    password = await get_password(ops_test, database_app_name=database_app_name)
    address = get_unit_address(ops_test, primary)
    logger.info("creating a table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute(
            "CREATE TABLE IF NOT EXISTS backup_table_1 (test_collumn INT );"
        )
    connection.close()

    # Run the "create backup" action.
    logger.info("creating a backup")
    action = await ops_test.model.units.get(replica).run_action("create-backup")
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    await ops_test.model.wait_for_idle(
        apps=[database_app_name, s3_integrator_app_name], status="active", timeout=1000
    )

    # With a stable cluster, Run the "create backup" action
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(replica).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    # 5 lines for header output, 1 backup line ==> 6 total lines
    assert len(backups.split("\n")) == 6, "full backup is not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Write some data.
    logger.info("creating a second table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
    connection.close()

    # Run the "create backup" action.
    logger.info("creating a backup")
    action = await ops_test.model.units.get(replica).run_action(
        "create-backup", **{"type": "differential"}
    )
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Run the "list backups" action.
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(replica).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    # 5 lines for header output, 2 backup lines ==> 7 total lines
    assert len(backups.split("\n")) == 7, "differential backup is not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Write some data.
    logger.info("creating a second table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE backup_table_3 (test_collumn INT );")
    connection.close()
    # Scale down to be able to restore.
    async with ops_test.fast_forward():
        await ops_test.model.destroy_unit(replica)
        await ops_test.model.block_until(
            lambda: len(ops_test.model.applications[database_app_name].units) == 1
        )

    for unit in ops_test.model.applications[database_app_name].units:
        remaining_unit = unit
        break

    # Run the "restore backup" action for differential backup.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            last_diff_backup = backups.split("\n")[-1]
            backup_id = last_diff_backup.split()[0]
            action = await remaining_unit.run_action("restore", **{"backup-id": backup_id})
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Check that the backup was correctly restored by having only the first created table.
    logger.info("checking that the backup was correctly restored")
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_2' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_3');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_3' exists"
        )
    connection.close()

    # Run the "restore backup" action for full backup.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            last_full_backup = backups.split("\n")[-2]
            backup_id = last_full_backup.split()[0]
            action = await remaining_unit.run_action("restore", **{"backup-id": backup_id})
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Check that the backup was correctly restored by having only the first created table.
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    logger.info("checking that the backup was correctly restored")
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_2' exists"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_3');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_3' exists"
        )
    connection.close()
