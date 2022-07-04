#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import os
import tempfile
import zipfile
from pathlib import Path
from typing import List

import psycopg2
import yaml
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]


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


async def check_database_creation(ops_test: OpsTest, databases: List[str]) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        databases: List of database names that should have been created
    """
    password = await get_postgres_password(ops_test)

    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        unit_address = await unit.get_public_address()

        for database in databases:
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


async def deploy_and_relate_bundle_with_postgresql(
    ops_test: OpsTest,
    bundle: str,
    overlay_path: str,
    application_name: str,
) -> str:
    """Helper function to deploy and relate a bundle with PostgreSQL.

    Args:
        ops_test: The ops test framework.
        bundle: Bundle identifier.
        overlay_path: Path to an overlay for the bundle.
        application_name: The name of the application to check for
            an active state after the deployment.
    """
    # Deploy the bundle.
    with tempfile.NamedTemporaryFile() as original:
        print(original.name)
        # print(patched.name)
        # Download the original bundle.
        await ops_test.juju("download", bundle, "--filepath", original.name)
        # Open the bundle compressed file and update the contents of the bundle.yaml file.
        with zipfile.ZipFile(original.name, "r") as archive:
            bundle_yaml = archive.read("bundle.yaml")
            data = yaml.load(bundle_yaml, Loader=yaml.FullLoader)
            print(data["services"]["postgresql"])
            print(os.getcwd())
            del data["services"]["postgresql"]
            print(data["relations"])
            data["relations"].remove(['landscape-server:db', 'postgresql:db-admin'])
            with open("./bundle.yaml", "w") as patched:
                patched.write(yaml.dump(data))
            print(yaml.dump(data))
            await ops_test.model.deploy(
                patched.name,
            )
    # Relate application to PostgreSQL.
    relation = await ops_test.model.relate(
        f"{application_name}", f"{DATABASE_APP_NAME}:db-admin"
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


async def get_postgres_password(ops_test: OpsTest):
    """Retrieve the postgres user password using the action."""
    unit = ops_test.model.units.get(f"{DATABASE_APP_NAME}/0")
    action = await unit.run_action("get-initial-password")
    result = await action.wait()
    return result.results["postgres-password"]


async def prepare_overlay(ops_test: OpsTest, overlay: str) -> str:
    """Downloads a bundle and generates a version of it without PostgreSQL.

    Args:
        ops_test: The ops test framework instance.
        overlay: The identifier of the overlay.

    Returns:
        Temporary path of the generated overlay.
    """
    # with tempfile.NamedTemporaryFile(delete=False) as temp:
    #     print(temp.name)
    #     # Download the original bundle.
    #     await ops_test.juju("download", overlay, "--filepath", temp.name)
    #     # Open the bundle compressed file and update the contents of the bundle.yaml file.
    #     with zipfile.ZipFile(temp.name, "r") as archive, archive.read("bundle.yaml") as bundle:
    #         data = yaml.load(bundle, Loader=yaml.FullLoader)
    #         print(data)
