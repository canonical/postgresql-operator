#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import psycopg2 as psycopg2
import pytest as pytest
from juju.errors import JujuUnitError
from mailmanclient import Client
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from . import markers
from .helpers import (
    APPLICATION_NAME,
    CHARM_SERIES,
    DATABASE_APP_NAME,
    build_connection_string,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_application_with_postgresql,
    deploy_and_relate_bundle_with_postgresql,
    find_unit,
    get_leader_unit,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)

LIVEPATCH_APP_NAME = "livepatch"
MAILMAN3_CORE_APP_NAME = "mailman3-core"
APPLICATION_UNITS = 1
DATABASE_UNITS = 2
RELATION_NAME = "db"

ROLES_BLOCKING_MESSAGE = (
    "roles requested through relation, use postgresql_client interface instead"
)


@pytest.mark.group(1)
async def test_mailman3_core_db(ops_test: OpsTest, charm: str) -> None:
    """Deploy Mailman3 Core to test the 'db' relation."""
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=DATABASE_UNITS,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        )

        # Wait until the PostgreSQL charm is successfully deployed.
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
        )
        await check_databases_creation(ops_test, ["mailman3"])

        mailman3_core_users = [f"relation-{relation_id}"]

        await check_database_users_existence(ops_test, mailman3_core_users, [])

        # Assert Mailman3 Core is configured to use PostgreSQL instead of SQLite.
        mailman_unit = ops_test.model.applications[MAILMAN3_CORE_APP_NAME].units[0]
        result = await run_command_on_unit(ops_test, mailman_unit.name, "mailman info")
        assert "db url: postgres://" in result

        # Do some CRUD operations using Mailman3 Core client.
        domain_name = "canonical.com"
        list_name = "postgresql-list"
        credentials = (
            result.split("credentials: ")[1].strip().split(":")
        )  # This outputs a list containing username and password.
        client = Client(
            f"http://{mailman_unit.public_address}:8001/3.1", credentials[0], credentials[1]
        )

        # Create a domain and list the domains to check that the new one is there.
        domain = client.create_domain(domain_name)
        assert domain_name in [domain.mail_host for domain in client.domains]

        # Update the domain by creating a mailing list into it.
        mailing_list = domain.create_list(list_name)
        assert mailing_list.fqdn_listname in [
            mailing_list.fqdn_listname for mailing_list in domain.lists
        ]

        # Delete the domain and check that the change was persisted.
        domain.delete()
        assert domain_name not in [domain.mail_host for domain in client.domains]


@pytest.mark.group(1)
async def test_relation_data_is_updated_correctly_when_scaling(ops_test: OpsTest):
    """Test that relation data, like connection data, is updated correctly when scaling."""
    # Retrieve the list of current database unit names.
    units_to_remove = [unit.name for unit in ops_test.model.applications[DATABASE_APP_NAME].units]

    async with ops_test.fast_forward():
        # Add two more units.
        await ops_test.model.applications[DATABASE_APP_NAME].add_units(2)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1500, wait_for_exact_units=4
        )

        # Remove the original units.
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(*units_to_remove)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=3000, wait_for_exact_units=2
        )

    # Get the updated connection data and assert it can be used
    # to write and read some data properly.
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

    # Connect to the database using the primary connection string.
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

    # Connect to the database using the replica endpoint.
    with psycopg2.connect(replica_connection_string) as connection:
        with connection.cursor() as cursor:
            # Read some data.
            cursor.execute("SELECT data FROM test;")
            data = cursor.fetchone()
            assert data[0] == "some data"

            # Try to alter some data in a read-only transaction.
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cursor.execute("DROP TABLE test;")
    connection.close()

    # Remove the relation and test that its user was deleted
    # (by checking that the connection string doesn't work anymore).
    async with ops_test.fast_forward():
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:{RELATION_NAME}", f"{MAILMAN3_CORE_APP_NAME}:{RELATION_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(10)):
        with attempt:
            with pytest.raises(psycopg2.OperationalError):
                psycopg2.connect(primary_connection_string)


@pytest.mark.group(1)
@pytest.mark.skip(reason="Should be ported and moved to the new relation tests")
async def test_nextcloud_db_blocked(ops_test: OpsTest, charm: str) -> None:
    async with ops_test.fast_forward():
        # Deploy Nextcloud.
        await ops_test.model.deploy(
            "nextcloud",
            channel="edge",
            application_name="nextcloud",
            num_units=APPLICATION_UNITS,
        )
        await ops_test.model.wait_for_idle(
            apps=["nextcloud"],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )

        await ops_test.model.relate("nextcloud:db", f"{DATABASE_APP_NAME}:db")

        # Only the leader will block
        leader_unit = await find_unit(ops_test, DATABASE_APP_NAME, True)

        try:
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME],
                status="blocked",
                raise_on_blocked=True,
                timeout=1000,
            )
            assert False, "Leader didn't block"
        except JujuUnitError:
            pass

        assert (
            leader_unit.workload_status_message
            == "extensions requested through relation, enable them through config options"
        )

        await ops_test.model.remove_application("nextcloud", block_until_done=True)


@pytest.mark.group(1)
async def test_sentry_db_blocked(ops_test: OpsTest, charm: str) -> None:
    async with ops_test.fast_forward():
        # Deploy Sentry and its dependencies.
        await asyncio.gather(
            ops_test.model.deploy(
                "omnivector-sentry", application_name="sentry1", series="bionic"
            ),
            ops_test.model.deploy("haproxy", series="focal"),
            ops_test.model.deploy("omnivector-redis", application_name="redis", series="bionic"),
        )
        await ops_test.model.wait_for_idle(
            apps=["sentry1"],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )
        await asyncio.gather(
            ops_test.model.relate("sentry1", "redis"),
            ops_test.model.relate("sentry1", f"{DATABASE_APP_NAME}:db"),
            ops_test.model.relate("sentry1", "haproxy"),
        )

        # Only the leader will block
        leader_unit = await find_unit(ops_test, DATABASE_APP_NAME, True)

        try:
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME],
                status="blocked",
                raise_on_blocked=True,
                timeout=1000,
            )
            assert False, "Leader didn't block"
        except JujuUnitError:
            pass

        assert (
            leader_unit.workload_status_message
            == "extensions requested through relation, enable them through config options"
        )

        # Verify that the charm unblocks when the extensions are enabled after being blocked
        # due to disabled extensions.
        logger.info("Verifying that the charm unblocks when the extensions are enabled")
        config = {"plugin_citext_enable": "True"}
        await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, "sentry1"],
            status="active",
            raise_on_blocked=False,
            idle_period=15,
        )

        # Verify that the charm doesn't block when the extensions are enabled
        # (another sentry deployment is used because it doesn't request a database
        # again after the relation with the PostgreSQL charm is destroyed and reestablished).
        logger.info("Verifying that the charm doesn't block when the extensions are enabled")
        await asyncio.gather(
            ops_test.model.remove_application("sentry1", block_until_done=True),
            ops_test.model.deploy(
                "omnivector-sentry", application_name="sentry2", series="bionic"
            ),
        )
        await asyncio.gather(
            ops_test.model.relate("sentry2", "redis"),
            ops_test.model.relate("sentry2", f"{DATABASE_APP_NAME}:db"),
            ops_test.model.relate("sentry2", "haproxy"),
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, "sentry2"], status="active", raise_on_blocked=False
        )

        await asyncio.gather(
            ops_test.model.remove_application("redis", block_until_done=True),
            ops_test.model.remove_application("sentry2", block_until_done=True),
            ops_test.model.remove_application("haproxy", block_until_done=True),
        )


@pytest.mark.group(1)
async def test_roles_blocking(ops_test: OpsTest, charm: str) -> None:
    await ops_test.model.deploy(
        APPLICATION_NAME,
        application_name=APPLICATION_NAME,
        config={"legacy_roles": True},
        series=CHARM_SERIES,
        channel="edge",
    )
    await ops_test.model.deploy(
        APPLICATION_NAME,
        application_name=f"{APPLICATION_NAME}2",
        config={"legacy_roles": True},
        series=CHARM_SERIES,
        channel="edge",
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, APPLICATION_NAME, f"{APPLICATION_NAME}2"],
        status="active",
        timeout=1000,
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

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.group(1)
@pytest.mark.unstable
async def test_weebl_db(ops_test: OpsTest, charm: str) -> None:
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            "weebl",
            application_name="weebl",
            num_units=APPLICATION_UNITS,
        )
        await ops_test.model.wait_for_idle(
            apps=["weebl"],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )

        await ops_test.model.relate("weebl:database", f"{DATABASE_APP_NAME}:db")

        await ops_test.model.wait_for_idle(
            apps=["weebl", DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )

        await ops_test.model.remove_application("weebl", block_until_done=True)


@markers.juju2
@pytest.mark.group(1)
async def test_canonical_livepatch_onprem_bundle_db(ops_test: OpsTest) -> None:
    # Deploy and test the Livepatch onprem bundle (using this PostgreSQL charm
    # and an overlay to make the Ubuntu Advantage charm work with PostgreSQL).
    # We intentionally wait for the `✘ sync_token not set` status message as we
    # aren't providing an Ubuntu Pro token (as this is just a test to ensure
    # the database works in the context of the relation with the Livepatch charm).
    overlay = {
        "applications": {"ubuntu-advantage": {"charm": "ubuntu-advantage", "series": CHARM_SERIES}}
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
