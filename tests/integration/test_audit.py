#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .adapters import JujuFixture
from .jubilant_helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    run_command_on_unit,
)
from .new_relations.jubilant_helpers import build_connection_string

logger = logging.getLogger(__name__)

RELATION_ENDPOINT = "database"


@pytest.mark.abort_on_fail
def test_audit_plugin(juju: JujuFixture, charm) -> None:
    """Test the audit plugin."""
    juju.ext.model.deploy(charm, config={"profile": "testing"})
    juju.ext.model.deploy(APPLICATION_NAME, channel="latest/edge", series="noble")
    juju.ext.model.relate(f"{APPLICATION_NAME}:{RELATION_ENDPOINT}", DATABASE_APP_NAME)
    with juju.ext.fast_forward():
        juju.ext.model.wait_for_idle(apps=[APPLICATION_NAME, DATABASE_APP_NAME], status="active")

    logger.info("Checking that the audit plugin is enabled")
    connection_string = build_connection_string(juju, APPLICATION_NAME, RELATION_ENDPOINT)
    connection = None
    try:
        connection = psycopg2.connect(connection_string)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test2(value TEXT);")
            cursor.execute("GRANT SELECT ON test2 TO PUBLIC;")
            cursor.execute("SET TIME ZONE 'Europe/Rome';")
    finally:
        if connection is not None:
            connection.close()
    unit_name = f"{DATABASE_APP_NAME}/0"
    for attempt in Retrying(stop=stop_after_delay(90), wait=wait_fixed(10), reraise=True):
        with attempt:
            try:
                logs = run_command_on_unit(
                    juju,
                    unit_name,
                    "sudo grep AUDIT /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log",
                )
                assert "MISC,BEGIN,,,BEGIN" in logs
                assert (
                    "DDL,CREATE TABLE,TABLE,public.test2,CREATE TABLE test2(value TEXT);" in logs
                )
                assert "ROLE,GRANT,TABLE,,GRANT SELECT ON test2 TO PUBLIC;" in logs
                assert "MISC,SET,,,SET TIME ZONE 'Europe/Rome';" in logs
            except Exception:
                assert False, "Audit logs were not found when the plugin is enabled."

    logger.info("Disabling the audit plugin")
    juju.ext.model.applications[DATABASE_APP_NAME].set_config({"plugin_audit_enable": "False"})
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    logger.info("Removing the previous logs")
    run_command_on_unit(
        juju,
        unit_name,
        "rm /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log",
    )

    logger.info("Checking that the audit plugin is disabled")
    try:
        connection = psycopg2.connect(connection_string)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test1(value TEXT);")
            cursor.execute("GRANT SELECT ON test1 TO PUBLIC;")
            cursor.execute("SET TIME ZONE 'Europe/Rome';")
    finally:
        if connection is not None:
            connection.close()
    try:
        logs = run_command_on_unit(
            juju,
            unit_name,
            "sudo grep AUDIT /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log",
        )
    except Exception:
        pass
    else:
        logger.info(f"Logs: {logs}")
        assert False, "Audit logs were found when the plugin is disabled."
