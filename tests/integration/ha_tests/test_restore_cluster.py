#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from datetime import datetime

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    METADATA,
    add_unit_with_storage,
    app_name,
    check_writes,
    is_replica,
    reused_storage,
    secondary_up_to_date,
    start_continuous_writes,
    storage_id,
    storage_type,
)
from tests.integration.helpers import CHARM_SERIES

APP_NAME = METADATA["name"]
SECOND_APPLICATION = "second-cluster"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        charm = await ops_test.build_charm(".")
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                charm,
                num_units=3,
                series=CHARM_SERIES,
                storage={"pgdata": {"pool": "lxd-btrfs", "size": 2048}},
            )
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(
                charm, application_name=APPLICATION_NAME, series=CHARM_SERIES
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


async def test_storage_re_use(ops_test, continuous_writes):
    """Verifies that database units with attached storage correctly repurpose storage.

    It is not enough to verify that Juju attaches the storage. Hence test checks that the mongod
    properly uses the storage that was provided. (ie. doesn't just re-sync everything from
    primary, but instead computes a diff between current storage and primary storage.)
    """
    app = await app_name(ops_test)
    if storage_type(ops_test, app) == "rootfs":
        pytest.skip(
            "re-use of storage can only be used on deployments with persistent storage not on rootfs deployments"
        )

    # removing the only replica can be disastrous
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # remove a unit and attach it's storage to a new unit
    for unit in ops_test.model.applications[app].units:
        if is_replica(ops_test, unit.name):
            break
    unit_storage_id = storage_id(ops_test, unit.name)
    expected_units = len(ops_test.model.applications[app].units) - 1
    removal_time = datetime.utcnow()
    await ops_test.model.destroy_unit(unit.name)
    await ops_test.model.wait_for_idle(
        apps=[app], status="active", timeout=1000, wait_for_exact_units=expected_units
    )
    new_unit = await add_unit_with_storage(ops_test, app, unit_storage_id)

    assert await reused_storage(
        ops_test, new_unit.name, removal_time
    ), "attached storage not properly re-used by Postgresql."

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await check_writes(ops_test)

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, new_unit.name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."
