#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_application_with_postgresql,
)

logger = logging.getLogger(__name__)

MAILMAN3_CORE_APP_NAME = "mailman3-core"
ANOTHER_MAILMAN3_CORE_APP_NAME = "another-mailman3-core"
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

    # Deploy and test the first deployment of Mailman3 Core.
    relation_id = await deploy_and_relate_application_with_postgresql(
        ops_test, "mailman3-core", MAILMAN3_CORE_APP_NAME, APPLICATION_UNITS
    )
    await check_databases_creation(
        ops_test,
        [
            "mailman3",
        ],
    )

    mailman3_core_users = [f"relation_id_{relation_id}"]

    await check_database_users_existence(ops_test, mailman3_core_users, [])

    # Deploy and test another deployment of Mailman3 Core.
    another_relation_id = await deploy_and_relate_application_with_postgresql(
        ops_test,
        "mailman3-core",
        ANOTHER_MAILMAN3_CORE_APP_NAME,
        APPLICATION_UNITS,
    )
    # In this case, the database name is the same as in the first deployment
    # because it's a fixed value in Mailman3 Core charm.
    await check_databases_creation(ops_test, ["mailman3"])

    another_mailman3_core_users = [f"relation_id_{another_relation_id}"]

    await check_database_users_existence(
        ops_test, mailman3_core_users + another_mailman3_core_users, []
    )

    # Scale down the second deployment of Mailman3 Core and confirm that the first deployment
    # is still active.
    await ops_test.model.remove_application(ANOTHER_MAILMAN3_CORE_APP_NAME, block_until_done=True)

    another_mailman3_core_users = []
    await check_database_users_existence(
        ops_test, mailman3_core_users, another_mailman3_core_users
    )

    # # Remove the first deployment of Mailman3 Core.
    # await ops_test.model.remove_application(MAILMAN3_CORE_APP_NAME, block_until_done=True)
    #
    # # Remove the PostgreSQL application.
    # await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
