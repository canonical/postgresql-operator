#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import time

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import (
    CHARM_SERIES,
    check_patroni,
    get_password,
    restart_patroni,
    set_password,
)

APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
@pytest.mark.password_rotation_tests
@pytest.mark.skip_if_deployed
async def test_deploy_active(ops_test: OpsTest):
    """Build the charm and deploy it."""
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={"patroni": "patroni.tar.gz"},
            application_name=APP_NAME,
            num_units=3,
            series=CHARM_SERIES,
        )
        await ops_test.juju("attach-resource", APP_NAME, "patroni=patroni.tar.gz")
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)


@pytest.mark.password_rotation_tests
async def test_password_rotation(ops_test: OpsTest):
    """Test password rotation action."""
    # Get the initial passwords set for the system users.
    any_unit_name = ops_test.model.applications[APP_NAME].units[0].name
    superuser_password = await get_password(ops_test, any_unit_name)
    replication_password = await get_password(ops_test, any_unit_name, "replication")

    # Get the leader unit name (because passwords can only be set through it).
    leader = None
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader = unit.name
            break

    # Change both passwords.
    result = await set_password(ops_test, unit_name=leader)
    assert "operator-password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # For replication, generate a specific password and pass it to the action.
    new_replication_password = "test-password"
    result = await set_password(
        ops_test, unit_name=leader, username="replication", password=new_replication_password
    )
    assert "replication-password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    new_superuser_password = await get_password(ops_test, any_unit_name)

    assert superuser_password != new_superuser_password
    assert new_replication_password == await get_password(ops_test, any_unit_name, "replication")
    assert replication_password != new_replication_password

    # Restart Patroni on any non-leader unit and check that
    # Patroni and PostgreSQL continue to work.
    restart_time = time.time()
    for unit in ops_test.model.applications[APP_NAME].units:
        if not await unit.is_leader_from_status():
            restart_patroni(ops_test, unit.name)
            assert check_patroni(ops_test, unit.name, restart_time)
