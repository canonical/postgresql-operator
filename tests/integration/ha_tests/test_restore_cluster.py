#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from datetime import datetime

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.helpers import (
    METADATA,
    add_unit_with_storage,
    app_name,
    reused_storage,
    storage_id,
    storage_type,
)
from tests.integration.helpers import CHARM_SERIES, get_password, set_password

APP_NAME = METADATA["name"]
SECOND_APPLICATION = "second-cluster"

logger = logging.getLogger(__name__)

charm = None


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        global charm
        charm = await ops_test.build_charm(".")
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                charm,
                # num_units=3,
                num_units=1,
                series=CHARM_SERIES,
                storage={"pgdata": {"pool": "lxd-btrfs", "size": 2048}},
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


async def test_cluster_restore(ops_test):
    """Recreates the cluster from storage volumes."""
    app = await app_name(ops_test)
    if storage_type(ops_test, app) == "rootfs":
        pytest.skip(
            "re-use of storage can only be used on deployments with persistent storage not on rootfs deployments"
        )

    # Deploy a second cluster
    global charm
    if not charm:
        charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(
        charm, application_name=SECOND_APPLICATION, num_units=None, series=CHARM_SERIES
    )

    logger.info("Downscaling the existing cluster")
    password = await get_password(ops_test, ops_test.model.applications[app].units[0].name)
    storages = []
    removal_times = []
    for unit in ops_test.model.applications[app].units:
        storages.append(storage_id(ops_test, unit.name))
        removal_times.append(datetime.utcnow())
        await ops_test.model.destroy_unit(unit.name)

    await ops_test.model.remove_application(app, block_until_done=True)

    # Recreate cluster
    logger.info("Upscaling the second cluster with the old data")
    for i in range(len(storages)):
        unit = await add_unit_with_storage(ops_test, SECOND_APPLICATION, storages[i])
        await set_password(ops_test, unit.name, password=password)
        removal_time = removal_times[i]
        assert await reused_storage(
            ops_test, unit.name, removal_time
        ), "attached storage not properly re-used by Postgresql."
