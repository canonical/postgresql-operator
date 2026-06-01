#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from time import sleep
from typing import get_args

import psycopg2
import pytest
import requests
from psycopg2 import sql
from tenacity import Retrying, stop_after_attempt, wait_exponential, wait_fixed

from locales import SNAP_LOCALES

from .adapters import JujuFixture
from .jubilant_helpers import (
    DATABASE_APP_NAME,
    STORAGE_PATH,
    check_cluster_members,
    convert_records_to_dict,
    db_connect,
    find_unit,
    get_cluster_roles,
    get_password,
    get_primary,
    get_unit_address,
    run_command_on_unit,
    scale_application,
    switchover,
)

logger = logging.getLogger(__name__)

UNIT_IDS = [0, 1, 2]


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
def test_deploy(juju: JujuFixture, charm: str):
    """Deploy the charm-under-test.

    Assert on the unit status before any relations/configurations take place.
    """
    # Deploy the charm with Patroni resource.
    juju.ext.model.deploy(
        charm,
        application_name=DATABASE_APP_NAME,
        num_units=3,
        config={"profile": "testing"},
    )

    # Reducing the update status frequency to speed up the triggering of deferred events.
    juju.ext.model.set_config({"update-status-hook-interval": "10s"})

    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)
    assert juju.ext.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_database_is_up(juju: JujuFixture, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = get_unit_address(juju, f"{DATABASE_APP_NAME}/{unit_id}")
    result = requests.get(f"https://{host}:8008/health", verify=False)
    assert result.status_code == 200


@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_exporter_is_up(juju: JujuFixture, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = get_unit_address(juju, f"{DATABASE_APP_NAME}/{unit_id}")
    result = requests.get(f"http://{host}:9187/metrics")
    assert result.status_code == 200
    assert "pg_exporter_last_scrape_error 0" in result.content.decode("utf8"), (
        "Scrape error in postgresql_prometheus_exporter"
    )


@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_settings_are_correct(juju: JujuFixture, unit_id: int):
    # Connect to the PostgreSQL instance.
    # Retrieving the operator user password using the action.
    password = get_password()

    # Connect to PostgreSQL.
    host = get_unit_address(juju, f"{DATABASE_APP_NAME}/{unit_id}")
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
            "autovacuum",
            "data_directory",
            "cluster_name",
            "data_checksums",
            "fsync",
            "full_page_writes",
            "lc_messages",
            "listen_addresses",
            "log_autovacuum_min_duration",
            "log_checkpoints",
            "log_destination",
            "log_temp_files",
            "log_timezone",
            "max_connections",
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
    assert settings["autovacuum"] == "on"
    assert settings["cluster_name"] == DATABASE_APP_NAME
    assert settings["data_directory"] == STORAGE_PATH
    assert settings["data_checksums"] == "on"
    assert settings["fsync"] == "on"
    assert settings["full_page_writes"] == "on"
    assert settings["lc_messages"] == "en_US.UTF8"
    assert settings["listen_addresses"] == host
    assert settings["log_autovacuum_min_duration"] == "60000"
    assert settings["log_checkpoints"] == "on"
    assert settings["log_destination"] == "stderr"
    assert settings["log_temp_files"] == "1"
    assert settings["log_timezone"] == "UTC"
    assert settings["max_connections"] == "100"
    assert settings["wal_level"] == "logical"

    # Retrieve settings from Patroni REST API.
    result = requests.get(f"https://{host}:8008/config", verify=False)
    settings = result.json()

    # Validate each configuration related to Patroni
    assert settings["postgresql"]["use_pg_rewind"] is True
    assert settings["postgresql"]["remove_data_directory_on_rewind_failure"] is False
    assert settings["postgresql"]["remove_data_directory_on_diverged_timelines"] is False
    assert settings["loop_wait"] == 10
    assert settings["retry_timeout"] == 10
    assert settings["maximum_lag_on_failover"] == 1048576

    logger.warning("Asserting port ranges")
    unit = juju.ext.model.applications[DATABASE_APP_NAME].units[unit_id]
    assert unit.status.open_ports
    assert unit.status.open_ports[0] == "5432/tcp"


def test_postgresql_locales(juju: JujuFixture) -> None:
    raw_locales = run_command_on_unit(
        juju,
        juju.ext.model.applications[DATABASE_APP_NAME].units[0].name,
        "ls /snap/charmed-postgresql/current/usr/lib/locale",
    )
    locales = raw_locales.splitlines()
    locales.append("C")
    locales.sort()

    # Juju 2 has an extra empty element
    if "" in locales:
        locales.remove("")
    assert locales == list(get_args(SNAP_LOCALES))


def test_postgresql_parameters_change(juju: JujuFixture) -> None:
    """Test that's possible to change PostgreSQL parameters."""
    juju.ext.model.applications[DATABASE_APP_NAME].set_config({
        "memory_max_prepared_transactions": "100",
        "memory_shared_buffers": "32768",  # 2 * 128MB. Patroni may refuse the config if < 128MB
        "response_lc_monetary": "en_GB.utf8",
        "experimental_max_connections": "200",
    })
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", idle_period=30)
    password = get_password()

    # Connect to PostgreSQL.
    for unit_id in UNIT_IDS:
        host = get_unit_address(juju, f"{DATABASE_APP_NAME}/{unit_id}")
        logger.info("connecting to the database host: %s", host)
        try:
            with (
                psycopg2.connect(
                    f"dbname='postgres' user='operator' host='{host}' password='{password}' connect_timeout=1"
                ) as connection,
                connection.cursor() as cursor,
            ):
                settings_names = [
                    "max_prepared_transactions",
                    "shared_buffers",
                    "lc_monetary",
                    "max_connections",
                ]
                cursor.execute(
                    sql.SQL("SELECT name,setting FROM pg_settings WHERE name IN ({});").format(
                        sql.SQL(", ").join(sql.Placeholder() * len(settings_names))
                    ),
                    settings_names,
                )
                records = cursor.fetchall()
                settings = convert_records_to_dict(records)

                # Validate each configuration set by Patroni on PostgreSQL.
                assert settings["max_prepared_transactions"] == "100"
                assert settings["shared_buffers"] == "32768"
                assert settings["lc_monetary"] == "en_GB.utf8"
                assert settings["max_connections"] == "200"
        finally:
            connection.close()


def test_scale_down_and_up(juju: JujuFixture):
    """Test data is replicated to new units after a scale up."""
    # Ensure the initial number of units in the application.
    initial_scale = len(UNIT_IDS)
    scale_application(juju, DATABASE_APP_NAME, initial_scale)

    # Scale down the application.
    scale_application(juju, DATABASE_APP_NAME, initial_scale - 1)

    # Ensure the member was correctly removed from the cluster
    # (by comparing the cluster members and the current units).
    check_cluster_members(juju, DATABASE_APP_NAME)

    # Scale up the application (2 more units than the current scale).
    scale_application(juju, DATABASE_APP_NAME, initial_scale + 1)

    # Assert the correct members are part of the cluster.
    check_cluster_members(juju, DATABASE_APP_NAME)

    # Test the deletion of the unit that is both the leader and the primary.
    any_unit_name = juju.ext.model.applications[DATABASE_APP_NAME].units[0].name
    primary = get_primary(juju, any_unit_name)
    leader_unit = find_unit(juju, leader=True, application=DATABASE_APP_NAME)

    # Trigger a switchover if the primary and the leader are not the same unit.
    patroni_password = get_password("patroni")

    if primary != leader_unit.name:
        switchover(juju, primary, patroni_password, leader_unit.name)

        # Get the new primary unit.
        primary = get_primary(juju, any_unit_name)
        # Check that the primary changed.
        for attempt in Retrying(
            stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert primary == leader_unit.name

    juju.ext.model.applications[DATABASE_APP_NAME].destroy_units(leader_unit.name)
    juju.ext.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], status="active", timeout=1000, wait_for_exact_units=initial_scale
    )

    # Assert the correct members are part of the cluster.
    check_cluster_members(juju, DATABASE_APP_NAME)

    # Scale up the application (2 more units than the current scale).
    scale_application(juju, DATABASE_APP_NAME, initial_scale + 2)

    # Test the deletion of both the unit that is the leader and the unit that is the primary.
    any_unit_name = juju.ext.model.applications[DATABASE_APP_NAME].units[0].name
    primary = get_primary(juju, any_unit_name)
    leader_unit = find_unit(juju, DATABASE_APP_NAME, True)

    # Trigger a switchover if the primary and the leader are the same unit.
    if primary == leader_unit.name:
        switchover(juju, primary, patroni_password)

        # Get the new primary unit.
        primary = get_primary(juju, any_unit_name)
        # Check that the primary changed.
        for attempt in Retrying(
            stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert primary != leader_unit.name

    juju.ext.model.applications[DATABASE_APP_NAME].destroy_units(primary, leader_unit.name)
    juju.ext.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=2000,
        idle_period=30,
        wait_for_exact_units=initial_scale,
    )

    # Wait some time to elect a new primary.
    sleep(30)

    # Assert the correct members are part of the cluster.
    check_cluster_members(juju, DATABASE_APP_NAME)

    # End with the cluster having the initial number of units.
    scale_application(juju, DATABASE_APP_NAME, initial_scale)


def test_switchover_sync_standby(juju: JujuFixture):
    original_roles = get_cluster_roles(
        juju, juju.ext.model.applications[DATABASE_APP_NAME].units[0].name
    )
    run_action = juju.ext.model.units[original_roles["sync_standbys"][0]].run_action(
        "promote-to-primary", scope="unit"
    )
    run_action.wait()

    juju.ext.model.wait_for_idle(status="active", timeout=200)

    new_roles = get_cluster_roles(
        juju, juju.ext.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert new_roles["primaries"][0] == original_roles["sync_standbys"][0]


def test_persist_data_through_primary_deletion(juju: JujuFixture):
    """Test data persists through a primary deletion."""
    # Set a composite application name in order to test in more than one series at the same time.
    any_unit_name = juju.ext.model.applications[DATABASE_APP_NAME].units[0].name
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            primary = get_primary(juju, any_unit_name)
            password = get_password()

    # Write data to primary IP.
    host = get_unit_address(juju, primary)
    logger.info(f"connecting to primary {primary} on {host}")
    with db_connect(host, password) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE primarydeletiontest (testcol INT);")
    connection.close()

    # Remove one unit.
    juju.ext.model.destroy_unit(primary)
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)

    # Add the unit again.
    juju.ext.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=2000)

    # Testing write occurred to every postgres instance by reading from them
    for unit in juju.ext.model.applications[DATABASE_APP_NAME].units:
        host = unit.public_address
        logger.info("connecting to the database host: %s", host)
        with db_connect(host, password) as connection, connection.cursor() as cursor:
            # Ensure we can read from "primarydeletiontest" table
            cursor.execute("SELECT * FROM primarydeletiontest;")
        connection.close()
