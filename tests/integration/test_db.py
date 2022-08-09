#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from mailmanclient import Client
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_application_with_postgresql,
)

logger = logging.getLogger(__name__)

MAILMAN3_CORE_APP_NAME = "mailman3-core"
APPLICATION_UNITS = 1
DATABASE_UNITS = 3


async def test_mailman3_core_db(ops_test: OpsTest, charm: str) -> None:
    """Deploy Mailman3 Core to test the 'db' relation."""
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
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == DATABASE_UNITS

    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        assert unit.workload_status == "active"

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
    await check_databases_creation(
        ops_test,
        [
            "mailman3",
        ],
    )

    mailman3_core_users = [f"relation_id_{relation_id}"]

    await check_database_users_existence(ops_test, mailman3_core_users, [])

    # Assert Mailman3 Core is configured to use PostgreSQL instead of SQLite.
    unit = ops_test.model.applications[MAILMAN3_CORE_APP_NAME].units[0]
    action = await unit.run("mailman info")
    result = action.results.get("Stdout", None)
    assert "db url: postgres://" in result

    # Do some CRUD operations using Mailman3 Core client.
    domain_name = "canonical.com"
    list_name = "postgresql-list"
    credentials = (
        result.split("credentials: ")[1].strip().split(":")
    )  # This outputs a list containing username and password.
    client = Client(f"http://{unit.public_address}:8001/3.1", credentials[0], credentials[1])

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

    # Remove the deployment of Mailman3 Core.
    await ops_test.model.remove_application(MAILMAN3_CORE_APP_NAME, block_until_done=True)

    # Remove the PostgreSQL application.
    await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
