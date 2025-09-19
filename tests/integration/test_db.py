#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import psycopg2 as psycopg2
import pytest as pytest
from mailmanclient import Client
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from . import markers
from .helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    DATABASE_APP_NAME,
    assert_sync_standbys,
    build_connection_string,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_application_with_postgresql,
    deploy_and_relate_bundle_with_postgresql,
    get_leader_unit,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)

LIVEPATCH_APP_NAME = "livepatch"
MAILMAN3_CORE_APP_NAME = "mailman3-core"
APPLICATION_UNITS = 1
DATABASE_UNITS = 2
RELATION_NAME = "db"

EXTENSIONS_BLOCKING_MESSAGE = (
    "extensions requested through relation, enable them through config options"
)
ROLES_BLOCKING_MESSAGE = (
    "roles requested through relation, use postgresql_client interface instead"
)


@pytest.mark.abort_on_fail
async def test_mailman3_core_db(ops_test: OpsTest, charm: str) -> None:
    """Deploy Mailman3 Core to test the 'db' relation."""
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=DATABASE_UNITS,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )

        logger.info("Wait until the PostgreSQL charm is successfully deployed.")
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            timeout=1500,
            wait_for_exact_units=DATABASE_UNITS,
        )

        # Extra config option for Mailman3 Core.
        config = {"hostname": "example.org"}
        # Deploy and test the deployment of Mailman3 Core.
        relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test,
            "mailman3-core",
            MAILMAN3_CORE_APP_NAME,
            APPLICATION_UNITS,
            config,
            series="focal",
        )
        await check_databases_creation(ops_test, ["mailman3"])

        mailman3_core_users = [f"relation-{relation_id}"]

        await check_database_users_existence(ops_test, mailman3_core_users, [])

        logger.info("Assert Mailman3 Core is configured to use PostgreSQL instead of SQLite.")
        mailman_unit = ops_test.model.applications[MAILMAN3_CORE_APP_NAME].units[0]
        result = await run_command_on_unit(ops_test, mailman_unit.name, "mailman info")
        assert "db url: postgres://" in result

        logger.info("Do some CRUD operations using Mailman3 Core client.")
        domain_name = "canonical.com"
        list_name = "postgresql-list"
        credentials = (
            result.split("credentials: ")[1].strip().split(":")
        )  # This outputs a list containing username and password.
        client = Client(
            f"http://{mailman_unit.public_address}:8001/3.1", credentials[0], credentials[1]
        )

        logger.info("Create a domain and list the domains to check that the new one is there.")
        domain = client.create_domain(domain_name)
        assert domain_name in [domain.mail_host for domain in client.domains]

        logger.info("Update the domain by creating a mailing list into it.")
        mailing_list = domain.create_list(list_name)
        assert mailing_list.fqdn_listname in [
            mailing_list.fqdn_listname for mailing_list in domain.lists
        ]

        logger.info("Delete the domain and check that the change was persisted.")
        domain.delete()
        assert domain_name not in [domain.mail_host for domain in client.domains]


@pytest.mark.abort_on_fail
async def test_relation_data_is_updated_correctly_when_scaling(ops_test: OpsTest):
    """Test that relation data, like connection data, is updated correctly when scaling."""
    # Retrieve the list of current database unit names.
    units_to_remove = [unit.name for unit in ops_test.model.applications[DATABASE_APP_NAME].units]

    async with ops_test.fast_forward():
        logger.info("Add two more units.")
        await ops_test.model.applications[DATABASE_APP_NAME].add_units(2)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1500, wait_for_exact_units=4
        )

        assert_sync_standbys(
            ops_test.model.applications[DATABASE_APP_NAME].units[0].public_address, 2
        )

        logger.info("Remove the original units.")
        leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(*[
            unit for unit in units_to_remove if unit != leader_unit.name
        ])
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=600, wait_for_exact_units=3
        )
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(leader_unit.name)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            timeout=600,
            wait_for_exact_units=2,
            idle_period=30,
        )

    logger.info(
        "Get the updated connection data and assert it can be used to write and read some data properly."
    )
    database_unit_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    primary_connection_string = await build_connection_string(
        ops_test, MAILMAN3_CORE_APP_NAME, RELATION_NAME, remote_unit_name=database_unit_name
    )
    replica_connection_string = await build_connection_string(
        ops_test,
        MAILMAN3_CORE_APP_NAME,
        RELATION_NAME,
        read_only_endpoint=True,
        remote_unit_name=database_unit_name,
    )

    logger.info("Connect to the database using the primary connection string.")
    with psycopg2.connect(primary_connection_string) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            # Check that it's possible to write and read data from the database that
            # was created for the application.
            cursor.execute("DROP TABLE IF EXISTS test;")
            cursor.execute("CREATE TABLE test(data TEXT);")
            cursor.execute("INSERT INTO test(data) VALUES('some data');")
            cursor.execute("SELECT data FROM test;")
            data = cursor.fetchone()
            assert data[0] == "some data"
    connection.close()

    logger.info("Connect to the database using the replica endpoint.")
    with psycopg2.connect(replica_connection_string) as connection, connection.cursor() as cursor:
        # Read some data.
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "some data"

        # Try to alter some data in a read-only transaction.
        with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            cursor.execute("DROP TABLE test;")
    connection.close()

    logger.info(
        "Remove the relation and test that its user was deleted (by checking that the connection string doesn't work anymore)."
    )
    async with ops_test.fast_forward():
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:{RELATION_NAME}", f"{MAILMAN3_CORE_APP_NAME}:{RELATION_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(10)):
        with attempt, pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(primary_connection_string)


async def test_roles_blocking(ops_test: OpsTest, charm: str) -> None:
    await ops_test.model.deploy(
        APPLICATION_NAME,
        application_name=APPLICATION_NAME,
        config={"legacy_roles": True},
        base=CHARM_BASE,
        channel="edge",
    )
    await ops_test.model.deploy(
        APPLICATION_NAME,
        application_name=f"{APPLICATION_NAME}2",
        config={"legacy_roles": True},
        base=CHARM_BASE,
        channel="edge",
    )

    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, f"{APPLICATION_NAME}2"], status="blocked", timeout=1000
    )

    await asyncio.gather(
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"),
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"),
    )

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE

    logger.info("Verify that the charm remains blocked if there are other blocking relations")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"
    )

    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE

    logger.info("Verify that active status is restored when all blocking relations are gone")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"
    )

    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)


async def test_extensions_blocking(ops_test: OpsTest, charm: str) -> None:
    await asyncio.gather(
        ops_test.model.applications[APPLICATION_NAME].set_config({"legacy_roles": "False"}),
        ops_test.model.applications[f"{APPLICATION_NAME}2"].set_config({"legacy_roles": "False"}),
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, f"{APPLICATION_NAME}2"], status="blocked", timeout=1000
    )

    await asyncio.gather(
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"),
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"),
    )

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE

    logger.info("Verify that the charm remains blocked if there are other blocking relations")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"
    )

    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE

    logger.info("Verify that active status is restored when all blocking relations are gone")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"
    )

    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)


@markers.juju2
@pytest.mark.skip(reason="Unstable")
@markers.amd64_only  # canonical-livepatch-server charm (in bundle) not available for arm64
async def test_canonical_livepatch_onprem_bundle_db(ops_test: OpsTest) -> None:
    # Deploy and test the Livepatch onprem bundle (using this PostgreSQL charm
    # and an overlay to make the Ubuntu Advantage charm work with PostgreSQL).
    # We intentionally wait for the `✘ sync_token not set` status message as we
    # aren't providing an Ubuntu Pro token (as this is just a test to ensure
    # the database works in the context of the relation with the Livepatch charm).
    overlay = {
        "applications": {"ubuntu-advantage": {"charm": "ubuntu-advantage", "base": CHARM_BASE}}
    }
    await deploy_and_relate_bundle_with_postgresql(
        ops_test,
        "canonical-livepatch-onprem",
        LIVEPATCH_APP_NAME,
        relation_name="db",
        status="blocked",
        status_message="✘ sync_token not set",
        overlay=overlay,
    )

    action = await ops_test.model.units.get(f"{LIVEPATCH_APP_NAME}/0").run_action("schema-upgrade")
    await action.wait()
    assert action.results.get("Code") == "0", "schema-upgrade action hasn't succeeded"
