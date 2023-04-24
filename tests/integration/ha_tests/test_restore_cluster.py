#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.helpers import (
    METADATA,
    add_unit_with_storage,
    app_name,
    get_patroni_cluster,
    reused_storage,
    storage_id,
    storage_type,
)
from tests.integration.helpers import (
    CHARM_SERIES,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)

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
                num_units=3,
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

    # Write some data.
    primary = await get_primary(ops_test, f"{app}/0")
    password = await get_password(ops_test, primary)
    address = get_unit_address(ops_test, primary)
    logger.info("creating a table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute(
            "CREATE TABLE IF NOT EXISTS restore_table_1 (test_collumn INT );"
        )
    connection.close()

    logger.info("Downscaling the existing cluster")
    storages = []
    for unit in ops_test.model.applications[app].units:
        storages.append(storage_id(ops_test, unit.name))
        await ops_test.model.destroy_unit(unit.name)

    await ops_test.model.remove_application(app, block_until_done=True)

    # Recreate cluster
    logger.info("Upscaling the second cluster with the old data")
    password_set = False
    for i in range(len(storages)):
        if not password_set:
            unit = await add_unit_with_storage(ops_test, SECOND_APPLICATION, storages[i], password)
            password_set = True
        else:
            unit = await add_unit_with_storage(ops_test, SECOND_APPLICATION, storages[i])
        assert await reused_storage(
            ops_test, unit.name
        ), "attached storage not properly re-used by Postgresql."

    primary = await get_primary(ops_test, f"{SECOND_APPLICATION}/0")
    address = get_unit_address(ops_test, primary)
    logger.info("checking that data was persisted")
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'restore_table_1');"
        )
        assert cursor.fetchone()[
            0
        ], "data wasn't correctly restored: table 'restore_table_1' doesn't exist"
    connection.close()

    # check that there is only one primary
    cluster = get_patroni_cluster(
        ops_test.model.applications[SECOND_APPLICATION].units[0].public_address
    )
    primaries = [member for member in cluster["members"] if member["role"] == "primary"]
    assert len(primaries) == 1
