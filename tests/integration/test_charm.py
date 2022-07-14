#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging

import psycopg2
import pytest
import requests
from pytest_operator.plugin import OpsTest

from tests.helpers import STORAGE_PATH
from tests.integration.helpers import (
    APP_NAME,
    build_application_name,
    check_cluster_members,
    convert_records_to_dict,
    db_connect,
    get_application_units,
    get_postgres_password,
    get_primary,
    get_unit_address,
    scale_application,
)

logger = logging.getLogger(__name__)

SERIES = ["focal"]
UNIT_IDS = [0, 1, 2]


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest):
    """Build the charm-under-test."""
    # Build charm from local source folder.
    yield await ops_test.build_charm(".")


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("series", SERIES)
async def test_deploy(ops_test: OpsTest, charm: str, series: str):
    """Deploy the charm-under-test.

    Assert on the unit status before any relations/configurations take place.
    """
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = f"{APP_NAME}-{series}"

    # Deploy the charm with Patroni resource.
    resources = {"patroni": "patroni.tar.gz"}
    await ops_test.model.deploy(
        charm, resources=resources, application_name=application_name, series=series, num_units=3
    )
    # Attach the resource to the controller.
    await ops_test.juju("attach-resource", application_name, "patroni=patroni.tar.gz")

    # Reducing the update status frequency to speed up the triggering of deferred events.
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)
    assert ops_test.model.applications[application_name].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("series", SERIES)
@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_database_is_up(ops_test: OpsTest, series: str, unit_id: int):
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = build_application_name(series)

    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = await get_unit_address(ops_test, f"{application_name}/{unit_id}")
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200


@pytest.mark.parametrize("series", SERIES)
@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_settings_are_correct(ops_test: OpsTest, series: str, unit_id: int):
    # Connect to the PostgreSQL instance.
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = build_application_name(series)

    # Retrieving the postgres user password using the action.
    action = await ops_test.model.units.get(f"{application_name}/{unit_id}").run_action(
        "get-initial-password"
    )
    action = await action.wait()
    password = action.results["postgres-password"]

    # Connect to PostgreSQL.
    host = await get_unit_address(ops_test, f"{application_name}/{unit_id}")
    logger.info("connecting to the database host: %s", host)
    with psycopg2.connect(
        f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=1"
    ) as connection:
        assert connection.status == psycopg2.extensions.STATUS_READY

        # Retrieve settings from PostgreSQL pg_settings table.
        # Here the SQL query gets a key-value pair composed by the name of the setting
        # and its value, filtering the retrieved data to return only the settings
        # that were set by Patroni.
        cursor = connection.cursor()
        cursor.execute(
            """SELECT name,setting
                FROM pg_settings
                WHERE name IN
                ('data_directory', 'cluster_name', 'data_checksums', 'listen_addresses');"""
        )
        records = cursor.fetchall()
        settings = convert_records_to_dict(records)

    # Validate each configuration set by Patroni on PostgreSQL.
    assert settings["cluster_name"] == f"{APP_NAME}-{series}"
    assert settings["data_directory"] == f"{STORAGE_PATH}/pgdata"
    assert settings["data_checksums"] == "on"
    assert settings["listen_addresses"] == host

    # Retrieve settings from Patroni REST API.
    result = requests.get(f"http://{host}:8008/config")
    settings = result.json()

    # Validate each configuration related to Patroni
    assert settings["postgresql"]["use_pg_rewind"]
    assert settings["loop_wait"] == 10
    assert settings["retry_timeout"] == 10
    assert settings["maximum_lag_on_failover"] == 1048576


# @pytest.mark.parametrize("series", SERIES)
# async def test_scale_down_and_up(ops_test: OpsTest, series: str):
#     """Test data is replicated to new units after a scale up."""
#     # Set a composite application name in order to test in more than one series at the same time.
#     application_name = build_application_name(series)
#
#     # Ensure the initial number of units in the application.
#     initial_scale = len(UNIT_IDS)
#     await scale_application(ops_test, application_name, initial_scale)
#
#     # Scale down the application.
#     await scale_application(ops_test, application_name, initial_scale - 1)
#
#     # Ensure the member was correctly removed from the cluster
#     # (by comparing the cluster members and the current units).
#     any_unit_name = ops_test.model.applications[application_name].units[0].name
#     primary = await get_primary(ops_test, any_unit_name)
#     address = await get_unit_address(ops_test, primary)
#
#     # Assert the correct members are part of the cluster.
#     expected_cluster_members = get_application_units(ops_test, application_name)
#     check_cluster_members(address, expected_cluster_members)
#
#     # Scale up the application (2 more units than the current scale).
#     await scale_application(ops_test, application_name, initial_scale + 1)
#
#     # Assert the correct members are part of the cluster.
#     expected_cluster_members = get_application_units(ops_test, application_name)
#     check_cluster_members(address, expected_cluster_members)
#
#     # Scale the application to the initial scale.
#     await scale_application(ops_test, application_name, initial_scale)
#
#     # Assert the correct members are part of the cluster.
#     expected_cluster_members = get_application_units(ops_test, application_name)
#     check_cluster_members(address, expected_cluster_members)


@pytest.mark.parametrize("series", SERIES)
async def test_persist_data_through_graceful_restart(ops_test: OpsTest, series: str):
    """Test data persists through a graceful restart."""
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = build_application_name(series)
    any_unit_name = ops_test.model.applications[application_name].units[0].name
    primary = await get_primary(ops_test, any_unit_name)
    password = await get_postgres_password(ops_test, primary)

    # Write data to primary IP.
    host = await get_unit_address(ops_test, primary)
    logger.info(f"connecting to primary {primary} on {host}")
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE gracetest (testcol INT );")

    # Remove one unit.
    await ops_test.model.destroy_units(
        primary,
    )
    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)

    # Add the unit again.
    await ops_test.model.applications[application_name].add_unit(count=1)
    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)

    # Testing write occurred to every postgres instance by reading from them
    for unit in ops_test.model.applications[application_name].units:
        host = unit.public_address
        logger.info("connecting to the database host: %s", host)
        with db_connect(host=host, password=password) as connection:
            # Ensure we can read from "gracetest" table
            connection.cursor().execute("SELECT * FROM gracetest;")
