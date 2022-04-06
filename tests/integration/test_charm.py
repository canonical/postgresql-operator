#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import psycopg2
import pytest
import yaml
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import pull_content_from_unit_file, run_command_on_unit

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
POSTGRESQL_VERSIONS = {"focal": "12", "bionic": "10"}
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
        charm, resources=resources, application_name=application_name, num_units=2, series=series
    )
    # Attach the resource to the controller.
    await ops_test.juju("attach-resource", application_name, "patroni=patroni.tar.gz")

    # Issuing dummy update_status just to trigger an event.
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)
    assert len(ops_test.model.applications[application_name].units) == 2
    assert ops_test.model.applications[application_name].units[0].workload_status == "active"
    assert ops_test.model.applications[application_name].units[1].workload_status == "active"

    # Effectively disable the update status from firing.
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("series", SERIES)
async def test_config_files_are_correct(ops_test: OpsTest, series: str):
    # Get the expected contents from files.
    with open("src/pg_hba.conf") as file:
        expected_pg_hba_conf = file.read()
    with open("tests/data/postgresql.conf") as file:
        expected_postgresql_conf = file.read()

    # Pull the configuration files from each PostgreSQL instance.
    for unit in ops_test.model.applications[f"{APP_NAME}-{series}"].units:
        # Get the path of the PostgreSQL configuration directory based on the PostgreSQL version.
        conf_path = Path(f"/etc/postgresql/{POSTGRESQL_VERSIONS[series]}/main")

        # Check whether client authentication is correctly set up.
        unit_pg_hba_conf_data = await pull_content_from_unit_file(unit, f"{conf_path}/pg_hba.conf")
        assert unit_pg_hba_conf_data == expected_pg_hba_conf

        # Check that the remaining settings are as expected.
        unit_postgresql_conf_data = await pull_content_from_unit_file(
            unit, f"{conf_path}/conf.d/postgresql-operator.conf"
        )
        assert unit_postgresql_conf_data == expected_postgresql_conf


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("series", SERIES)
async def test_database_is_up(ops_test: OpsTest, series: str):
    # Set a composite application name in order to test in more than one series at the same time.
    application_name = f"{APP_NAME}-{series}"

    # Retrieving the postgres user password using the action.
    action = await ops_test.model.units.get(f"{application_name}/0").run_action(
        "get-initial-password"
    )
    action = await action.wait()
    password = action.results["postgres-password"]

    # Testing the connection to each PostgreSQL instance.
    for unit in ops_test.model.applications[application_name].units:
        # List clusters to assert there is only one cluster per unit.
        result = await run_command_on_unit(unit, "pg_lsclusters --no-header")
        clusters = result.splitlines()
        assert len(clusters) == 1

        cluster_data = clusters[0].split()
        # Check for correct cluster version.
        assert cluster_data[0] == POSTGRESQL_VERSIONS[series]
        # And check cluster status (online or down).
        assert cluster_data[3] == "online"

        # Then, test the connection.
        host = unit.public_address
        logger.info("connecting to the database host: %s", host)
        connection = psycopg2.connect(
            f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=1"
        )
        assert connection.status == psycopg2.extensions.STATUS_READY
        # TODO: check for correct number of members (master and standby replicas)
        # and replication status when replication is implemented.
        connection.close()
