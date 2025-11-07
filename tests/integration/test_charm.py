#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from time import sleep
from typing import get_args

import jubilant
import psycopg2
import pytest
import requests
from jubilant import Juju
from psycopg2 import sql

from locales import SNAP_LOCALES

from .helpers import (
    DATABASE_APP_NAME,
    STORAGE_PATH,
    convert_records_to_dict,
    db_connect,
)
from .high_availability.high_availability_helpers_new import (
    get_unit_ip,
    get_user_password,
    wait_for_apps_status,
)

DB_APP_NAME = "postgresql"
MINUTE_SECS = 60
UNIT_IDS = [0, 1, 2]


def test_deploy(juju: Juju, charm) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    logging.info("Deploying PostgreSQL cluster")
    juju.deploy(
        charm=charm,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        config={"profile": "testing"},
        num_units=3,
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_database_is_up(juju: Juju, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = get_unit_ip(juju, DB_APP_NAME, f"{DB_APP_NAME}/{unit_id}")
    result = requests.get(f"https://{host}:8008/health", verify=False)
    assert result.status_code == 200


@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_exporter_is_up(juju: Juju, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = get_unit_ip(juju, DB_APP_NAME, f"{DB_APP_NAME}/{unit_id}")
    result = requests.get(f"http://{host}:9187/metrics")
    assert result.status_code == 200
    assert "pg_exporter_last_scrape_error 0" in result.content.decode("utf8"), (
        "Scrape error in postgresql_prometheus_exporter"
    )


@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_settings_are_correct(juju: Juju, unit_id: int):
    # Connect to the PostgreSQL instance.
    # Retrieving the operator user password using the action.
    password = get_user_password(juju, DB_APP_NAME, "operator")

    # Connect to PostgreSQL.
    host = get_unit_ip(juju, DB_APP_NAME, f"{DB_APP_NAME}/{unit_id}")
    logging.info("connecting to the database host: %s", host)
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

    logging.warning("Asserting port ranges")
    unit = juju.status().apps[DATABASE_APP_NAME].units[f"{DB_APP_NAME}/{unit_id}"]
    assert unit.open_ports == ["5432/tcp"]


def test_postgresql_locales(juju: Juju) -> None:
    task = juju.exec("ls /snap/charmed-postgresql/current/usr/lib/locale", unit=f"{DB_APP_NAME}/0")
    task.raise_on_failure()
    locales = task.stdout.splitlines()
    locales.append("C")
    locales.sort()

    # Juju 2 has an extra empty element
    if "" in locales:
        locales.remove("")
    assert locales == list(get_args(SNAP_LOCALES))


def test_postgresql_parameters_change(juju: Juju) -> None:
    """Test that's possible to change PostgreSQL parameters."""
    juju.config(
        app=DATABASE_APP_NAME,
        values={
            "memory_max_prepared_transactions": "100",
            "memory_shared_buffers": "32768",  # 2 * 128MB. Patroni may refuse the config if < 128MB
            "response_lc_monetary": "en_GB.utf8",
            "experimental_max_connections": "200",
        },
    )
    sleep(5)
    juju.wait(ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME))
    password = get_user_password(juju, DB_APP_NAME, "operator")

    # Connect to PostgreSQL.
    for unit_id in UNIT_IDS:
        host = get_unit_ip(juju, DB_APP_NAME, f"{DB_APP_NAME}/{unit_id}")
        logging.info("connecting to the database host: %s", host)
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
