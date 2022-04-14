#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from typing import List

import psycopg2
import pytest
import requests
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA, STORAGE_PATH

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
SERIES = ["focal", "bionic"]


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
        charm, resources=resources, application_name=application_name, series=series
    )
    # Attach the resource to the controller.
    await ops_test.juju("attach-resource", application_name, "patroni=patroni.tar.gz")

    # Issuing dummy update_status just to trigger an event.
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)
    assert ops_test.model.applications[application_name].units[0].workload_status == "active"

    # Effectively disable the update status from firing.
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("series", SERIES)
async def test_database_is_up(ops_test: OpsTest, series: str):
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = build_application_name(series)

    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = ops_test.model.units.get(f"{application_name}/0").public_address
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200


@pytest.mark.parametrize("series", SERIES)
async def test_settings_are_correct(ops_test: OpsTest, series: str):
    # Connect to the PostgreSQL instance.
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = build_application_name(series)

    # Retrieving the postgres user password using the action.
    action = await ops_test.model.units.get(f"{application_name}/0").run_action(
        "get-initial-password"
    )
    action = await action.wait()
    password = action.results["postgres-password"]

    # Connect to PostgreSQL.
    host = ops_test.model.units.get(f"{application_name}/0").public_address
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


def build_application_name(series: str) -> str:
    """Return a composite application name combining application name and series."""
    return f"{APP_NAME}-{series}"


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts pyscopg2 records list to a dict."""
    dict = {}
    for record in records:
        # Add record tuple data to dict.
        dict[record[0]] = record[1]
    return dict
