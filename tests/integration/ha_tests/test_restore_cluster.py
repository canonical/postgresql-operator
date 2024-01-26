#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    CHARM_SERIES,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    set_password,
)
from .helpers import (
    add_unit_with_storage,
    get_patroni_cluster,
    reused_full_cluster_recovery_storage,
    storage_id,
)

FIRST_APPLICATION = "first-cluster"
SECOND_APPLICATION = "second-cluster"

logger = logging.getLogger(__name__)

charm = None


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy two PostgreSQL clusters."""
    # This is a potentially destructive test, so it shouldn't be run against existing clusters
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        # Deploy the first cluster with reusable storage
        await ops_test.model.deploy(
            charm,
            application_name=FIRST_APPLICATION,
            num_units=3,
            series=CHARM_SERIES,
            storage={"pgdata": {"pool": "lxd-btrfs", "size": 2048}},
            config={"profile": "testing"},
        )

        # Deploy the second cluster
        await ops_test.model.deploy(
            charm,
            application_name=SECOND_APPLICATION,
            num_units=1,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        )

        await ops_test.model.wait_for_idle(status="active", timeout=1500)

        # TODO have a better way to bootstrap clusters with existing storage
        primary = await get_primary(
            ops_test, ops_test.model.applications[FIRST_APPLICATION].units[0].name
        )
        password = await get_password(ops_test, primary)
        second_primary = ops_test.model.applications[SECOND_APPLICATION].units[0].name
        await set_password(ops_test, second_primary, password=password)
        await ops_test.model.destroy_unit(second_primary)


@pytest.mark.group(1)
async def test_cluster_restore(ops_test):
    """Recreates the cluster from storage volumes."""
    # Write some data.
    primary = await get_primary(
        ops_test, ops_test.model.applications[FIRST_APPLICATION].units[0].name
    )
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
    for unit in ops_test.model.applications[FIRST_APPLICATION].units:
        storages.append(storage_id(ops_test, unit.name))
        await ops_test.model.destroy_unit(unit.name)

    await ops_test.model.remove_application(FIRST_APPLICATION, block_until_done=True)

    # Recreate cluster
    logger.info("Upscaling the second cluster with the old data")
    for storage in storages:
        unit = await add_unit_with_storage(ops_test, SECOND_APPLICATION, storage)
        assert await reused_full_cluster_recovery_storage(
            ops_test, unit.name
        ), "attached storage not properly re-used by Postgresql."

    primary = await get_primary(
        ops_test, ops_test.model.applications[SECOND_APPLICATION].units[0].name
    )
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
    primaries = [member for member in cluster["members"] if member["role"] == "leader"]
    assert len(primaries) == 1, "There isn't just a single primary"

    # check that all units are member of the new cluster
    members = [member["name"] for member in cluster["members"]]
    for unit in ops_test.model.applications[SECOND_APPLICATION].units:
        assert unit.name.replace("/", "-") in members, "Unit missing from cluster"
    assert len(members) == len(storages), "Number of restored units and reused storages diverge"
