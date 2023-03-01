#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging

import psycopg2
import pytest
import requests
from psycopg2 import sql
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from tests.helpers import STORAGE_PATH
from tests.integration.helpers import (
    CHARM_SERIES,
    DATABASE_APP_NAME,
    check_cluster_members,
    convert_records_to_dict,
    db_connect,
    find_unit,
    get_password,
    get_primary,
    get_unit_address,
    scale_application,
    switchover,
)

logger = logging.getLogger(__name__)

UNIT_IDS = [0, 1, 2]


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_deploy(ops_test: OpsTest, charm: str):
    """Deploy the charm-under-test.

    Assert on the unit status before any relations/configurations take place.
    """
    # Deploy the charm with Patroni resource.
    resources = {"patroni": "patroni.tar.gz"}
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=DATABASE_APP_NAME,
        num_units=3,
        series=CHARM_SERIES,
    )
    # Attach the resource to the controller.
    await ops_test.juju("attach-resource", DATABASE_APP_NAME, "patroni=patroni.tar.gz")

    # Reducing the update status frequency to speed up the triggering of deferred events.
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
    assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_database_is_up(ops_test: OpsTest, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = get_unit_address(ops_test, f"{DATABASE_APP_NAME}/{unit_id}")
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_settings_are_correct(ops_test: OpsTest, unit_id: int):
    # Connect to the PostgreSQL instance.
    # Retrieving the operator user password using the action.
    any_unit_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    password = await get_password(ops_test, any_unit_name)

    # Connect to PostgreSQL.
    host = get_unit_address(ops_test, f"{DATABASE_APP_NAME}/{unit_id}")
    logger.info("connecting to the database host: %s", host)
    with db_connect(host, password) as connection:
        assert connection.status == psycopg2.extensions.STATUS_READY

        # Retrieve settings from PostgreSQL pg_settings table.
        # Here the SQL query gets a key-value pair composed by the name of the setting
        # and its value, filtering the retrieved data to return only the settings
        # that were set by Patroni.
        settings_names = [
            "archive_command",
            "archive_mode",
            "data_directory",
            "cluster_name",
            "data_checksums",
            "listen_addresses",
            "wal_level",
        ]
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT name,setting FROM pg_settings WHERE name IN ({});").format(
                    sql.SQL(", ").join(sql.Placeholder() * len(settings_names))
                ),
                settings_names,
            )
            records = cursor.fetchall()
            settings = convert_records_to_dict(records)
    connection.close()

    # Validate each configuration set by Patroni on PostgreSQL.
    assert settings["archive_command"] == "/bin/true"
    assert settings["archive_mode"] == "on"
    assert settings["cluster_name"] == DATABASE_APP_NAME
    assert settings["data_directory"] == f"{STORAGE_PATH}/pgdata"
    assert settings["data_checksums"] == "on"
    assert settings["listen_addresses"] == host
    assert settings["wal_level"] == "logical"

    # Retrieve settings from Patroni REST API.
    result = requests.get(f"http://{host}:8008/config")
    settings = result.json()

    # Validate each configuration related to Patroni
    assert settings["postgresql"]["use_pg_rewind"]
    assert settings["postgresql"]["remove_data_directory_on_rewind_failure"]
    assert settings["postgresql"]["remove_data_directory_on_diverged_timelines"]
    assert settings["loop_wait"] == 10
    assert settings["retry_timeout"] == 10
    assert settings["maximum_lag_on_failover"] == 1048576


async def test_scale_down_and_up(ops_test: OpsTest):
    """Test data is replicated to new units after a scale up."""
    # Ensure the initial number of units in the application.
    initial_scale = len(UNIT_IDS)
    await scale_application(ops_test, DATABASE_APP_NAME, initial_scale)

    # Scale down the application.
    await scale_application(ops_test, DATABASE_APP_NAME, initial_scale - 1)

    # Ensure the member was correctly removed from the cluster
    # (by comparing the cluster members and the current units).
    await check_cluster_members(ops_test, DATABASE_APP_NAME)

    # Scale up the application (2 more units than the current scale).
    await scale_application(ops_test, DATABASE_APP_NAME, initial_scale + 1)

    # Assert the correct members are part of the cluster.
    await check_cluster_members(ops_test, DATABASE_APP_NAME)

    # Test the deletion of the unit that is both the leader and the primary.
    any_unit_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    primary = await get_primary(ops_test, any_unit_name)
    leader_unit = await find_unit(ops_test, leader=True, application=DATABASE_APP_NAME)

    # Trigger a switchover if the primary and the leader are not the same unit.
    if primary != leader_unit.name:
        switchover(ops_test, primary, leader_unit.name)

        # Get the new primary unit.
        primary = await get_primary(ops_test, any_unit_name)
        # Check that the primary changed.
        for attempt in Retrying(
            stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert primary == leader_unit.name

    await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(leader_unit.name)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], status="active", timeout=1000, wait_for_exact_units=initial_scale
    )

    # Assert the correct members are part of the cluster.
    await check_cluster_members(ops_test, DATABASE_APP_NAME)

    # Scale up the application (2 more units than the current scale).
    await scale_application(ops_test, DATABASE_APP_NAME, initial_scale + 2)

    # Test the deletion of both the unit that is the leader and the unit that is the primary.
    any_unit_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    primary = await get_primary(ops_test, any_unit_name)
    leader_unit = await find_unit(ops_test, DATABASE_APP_NAME, True)

    # Trigger a switchover if the primary and the leader are the same unit.
    if primary == leader_unit.name:
        switchover(ops_test, primary)

        # Get the new primary unit.
        primary = await get_primary(ops_test, any_unit_name)
        # Check that the primary changed.
        for attempt in Retrying(
            stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert primary != leader_unit.name

    await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(primary, leader_unit.name)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=initial_scale,
    )

    # Assert the correct members are part of the cluster.
    await check_cluster_members(ops_test, DATABASE_APP_NAME)

    # End with the cluster having the initial number of units.
    await scale_application(ops_test, DATABASE_APP_NAME, initial_scale)


async def test_persist_data_through_primary_deletion(ops_test: OpsTest):
    """Test data persists through a primary deletion."""
    # Set a composite application name in order to test in more than one series at the same time.
    any_unit_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    primary = await get_primary(ops_test, any_unit_name)
    password = await get_password(ops_test, primary)

    # Write data to primary IP.
    host = get_unit_address(ops_test, primary)
    logger.info(f"connecting to primary {primary} on {host}")
    with db_connect(host, password) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE primarydeletiontest (testcol INT);")
    connection.close()

    # Remove one unit.
    await ops_test.model.destroy_units(
        primary,
    )
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

    # Add the unit again.
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

    # Testing write occurred to every postgres instance by reading from them
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        host = unit.public_address
        logger.info("connecting to the database host: %s", host)
        with db_connect(host, password) as connection:
            with connection.cursor() as cursor:
                # Ensure we can read from "primarydeletiontest" table
                cursor.execute("SELECT * FROM primarydeletiontest;")
        connection.close()
