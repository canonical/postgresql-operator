#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging

import psycopg2
import pytest
from landscape_api.base import HTTPError, run_query
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    build_connection_string,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_bundle_with_postgresql,
    ensure_correct_relation_data,
    get_landscape_api_credentials,
    get_machine_from_unit,
    get_password,
    get_primary,
    primary_changed,
    start_machine,
    stop_machine,
    switchover,
)

logger = logging.getLogger(__name__)

HAPROXY_APP_NAME = "haproxy"
LANDSCAPE_APP_NAME = "landscape-server"
RABBITMQ_APP_NAME = "rabbitmq-server"
DATABASE_UNITS = 3
RELATION_NAME = "db-admin"


@pytest.mark.group(1)
async def test_landscape_scalable_bundle_db(ops_test: OpsTest, charm: str) -> None:
    """Deploy Landscape Scalable Bundle to test the 'db-admin' relation."""
    await ops_test.model.deploy(
        charm,
        application_name=DATABASE_APP_NAME,
        num_units=DATABASE_UNITS,
        base=CHARM_BASE,
        config={"profile": "testing", "plugin_plpython3u_enable": "True"},
    )

    # Deploy and test the Landscape Scalable bundle (using this PostgreSQL charm).
    relation_id = await deploy_and_relate_bundle_with_postgresql(
        ops_test,
        "ch:landscape-scalable",
        LANDSCAPE_APP_NAME,
        main_application_num_units=2,
        relation_name=RELATION_NAME,
        timeout=3000,
    )
    await check_databases_creation(
        ops_test,
        [
            "landscape-standalone-account-1",
            "landscape-standalone-knowledge",
            "landscape-standalone-main",
            "landscape-standalone-package",
            "landscape-standalone-resource-1",
            "landscape-standalone-session",
        ],
    )

    landscape_users = [f"relation-{relation_id}"]

    await check_database_users_existence(ops_test, landscape_users, [])

    # Create the admin user on Landscape through configs.
    await ops_test.model.applications["landscape-server"].set_config({
        "admin_email": "admin@canonical.com",
        "admin_name": "Admin",
        "admin_password": "test1234",
    })
    await ops_test.model.wait_for_idle(
        apps=["landscape-server", DATABASE_APP_NAME],
        status="active",
        timeout=1200,
    )

    # Connect to the Landscape API through HAProxy and do some CRUD calls (without the update).
    key, secret = await get_landscape_api_credentials(ops_test)
    haproxy_unit = ops_test.model.applications[HAPROXY_APP_NAME].units[0]
    api_uri = f"https://{haproxy_unit.public_address}/api/"

    # Create a role and list the available roles later to check that the new one is there.
    role_name = "User1"
    run_query(key, secret, "CreateRole", {"name": role_name}, api_uri, False)
    api_response = run_query(key, secret, "GetRoles", {}, api_uri, False)
    assert role_name in [user["name"] for user in json.loads(api_response)]

    # Remove the role and assert it isn't part of the roles list anymore.
    run_query(key, secret, "RemoveRole", {"name": role_name}, api_uri, False)
    api_response = run_query(key, secret, "GetRoles", {}, api_uri, False)
    assert role_name not in [user["name"] for user in json.loads(api_response)]

    await ensure_correct_relation_data(ops_test, DATABASE_UNITS, LANDSCAPE_APP_NAME, RELATION_NAME)

    # Enable automatically-retry-hooks due to https://bugs.launchpad.net/juju/+bug/1999758
    # (the implemented workaround restarts the unit in the middle of the start hook,
    # so the hook fails, and it's not retried on CI).
    await ops_test.model.set_config({"automatically-retry-hooks": "true"})

    # Stop the primary unit machine.
    logger.info("restarting primary")
    former_primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    former_primary_machine = await get_machine_from_unit(ops_test, former_primary)
    patroni_password = await get_password(ops_test, former_primary, "patroni")

    await stop_machine(ops_test, former_primary_machine)

    # Await for a new primary to be elected.
    assert await primary_changed(ops_test, former_primary)

    # Start the former primary unit machine again.
    await start_machine(ops_test, former_primary_machine)

    # Wait for the unit to be ready again. Some errors in the start hook may happen due to
    # rebooting the unit machine in the middle of a hook (what is needed when the issue from
    # https://bugs.launchpad.net/juju/+bug/1999758 happens).
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], status="active", timeout=1500, raise_on_error=False
    )

    await ensure_correct_relation_data(ops_test, DATABASE_UNITS, LANDSCAPE_APP_NAME, RELATION_NAME)

    # Trigger a switchover.
    logger.info("triggering a switchover")
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    switchover(ops_test, primary, patroni_password)

    # Await for a new primary to be elected.
    assert await primary_changed(ops_test, primary)

    await ensure_correct_relation_data(ops_test, DATABASE_UNITS, LANDSCAPE_APP_NAME, RELATION_NAME)

    # Trigger a config change to start the Landscape API service again.
    # The Landscape API was stopped after a new primary (postgresql) was elected.
    await ops_test.model.applications["landscape-server"].set_config({
        "admin_name": "Admin 1",
    })
    await ops_test.model.wait_for_idle(
        apps=["landscape-server", DATABASE_APP_NAME], timeout=1500, status="active"
    )

    # Create a role and list the available roles later to check that the new one is there.
    role_name = "User2"
    try:
        run_query(key, secret, "CreateRole", {"name": role_name}, api_uri, False)
    except HTTPError as e:
        assert False, f"error when trying to create role on Landscape: {e}"

    database_unit_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    connection_string = await build_connection_string(
        ops_test, LANDSCAPE_APP_NAME, RELATION_NAME, remote_unit_name=database_unit_name
    )

    # Remove the applications from the bundle.
    await ops_test.model.remove_application(LANDSCAPE_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(HAPROXY_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(RABBITMQ_APP_NAME, block_until_done=True)

    # Remove the relation and test that its user was deleted
    # (by checking that the connection string doesn't work anymore).
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(10)):
        with attempt, pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(connection_string)

    # Remove the PostgreSQL application.
    await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
