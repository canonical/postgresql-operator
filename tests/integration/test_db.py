#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from juju.errors import JujuUnitError
from mailmanclient import Client
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    build_connection_string,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_application_with_postgresql,
    find_unit,
)

logger = logging.getLogger(__name__)

MAILMAN3_CORE_APP_NAME = "mailman3-core"
APPLICATION_UNITS = 1
DATABASE_UNITS = 2
RELATION_NAME = "db"


@pytest.mark.db_relation_tests
async def test_mailman3_core_db(ops_test: OpsTest, charm: str) -> None:
    """Deploy Mailman3 Core to test the 'db' relation."""
    async with ops_test.fast_forward():
        resources = {"patroni": "patroni.tar.gz"}
        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=DATABASE_APP_NAME,
            num_units=DATABASE_UNITS,
        )
        # Attach the resource to the controller.
        await ops_test.juju("attach-resource", DATABASE_APP_NAME, "patroni=patroni.tar.gz")

        # Wait until the PostgreSQL charm is successfully deployed.
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            timeout=1000,
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
        action = await mailman_unit.run("mailman info")
        result = action.results.get("Stdout", None)
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


@pytest.mark.db_relation_tests
async def test_relation_data_is_updated_correctly_when_scaling(ops_test: OpsTest):
    """Test that relation data, like connection data, is updated correctly when scaling."""
    # Retrieve the list of current database unit names.
    units_to_remove = [unit.name for unit in ops_test.model.applications[DATABASE_APP_NAME].units]

    async with ops_test.fast_forward():
        # Add two more units.
        await ops_test.model.applications[DATABASE_APP_NAME].add_units(2)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1000, wait_for_exact_units=4
        )

        # Remove the original units.
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(*units_to_remove)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=3000, wait_for_exact_units=2
        )

        # Get the updated connection data and assert it can be used
        # to write and read some data properly.
        primary_connection_string = await build_connection_string(
            ops_test, MAILMAN3_CORE_APP_NAME, RELATION_NAME
        )
        replica_connection_string = await build_connection_string(
            ops_test, MAILMAN3_CORE_APP_NAME, RELATION_NAME, read_only_endpoint=True
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
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:{RELATION_NAME}", f"{MAILMAN3_CORE_APP_NAME}:{RELATION_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
        with pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(primary_connection_string)


@pytest.mark.db_relation_tests
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

        assert leader_unit.workload_status_message == "extensions requested through relation"

        await ops_test.model.remove_application("nextcloud", block_until_done=True)


@pytest.mark.db_relation_tests
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
