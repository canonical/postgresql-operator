#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather

import pytest
from pytest_operator.plugin import OpsTest

from .. import markers
from ..helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
)
from .helpers import (
    APPLICATION_NAME,
    app_name,
    are_writes_increasing,
    check_writes,
    get_cluster_roles,
    get_primary,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

charm = None


@markers.juju3
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy two PostgreSQL clusters."""
    # This is a potentially destructive test, so it shouldn't be run against existing clusters
    async with ops_test.fast_forward():
        # Deploy the first cluster with reusable storage
        await gather(
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=2,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                base=CHARM_BASE,
                channel="edge",
            ),
        )

        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        await ops_test.model.wait_for_idle(status="active", timeout=1500)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_stereo_primary(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    await start_continuous_writes(ops_test, app)
    logger.info("Deleting primary")
    primary = await get_primary(ops_test, app)
    await ops_test.model.destroy_unit(primary, force=True, destroy_storage=False, max_wait=1500)

    left_unit = ops_test.model.units[original_roles["sync_standbys"][0]]
    for left_unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        if left_unit.name not in original_roles["primaries"]:
            break

    await ops_test.model.block_until(
        lambda: left_unit.workload_status == "blocked"
        and left_unit.workload_status_message == "Raft majority loss, run: promote-to-primary",
        timeout=600,
    )

    run_action = (
        await ops_test.model.applications[DATABASE_APP_NAME]
        .units[0]
        .run_action("promote-to-primary", scope="unit", force=True)
    )
    await run_action.wait()

    await ops_test.model.wait_for_idle(status="active", timeout=600, idle_period=45)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await are_writes_increasing(ops_test, primary)

    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert len(new_roles["sync_standbys"]) == 1
    assert new_roles["primaries"][0] == original_roles["sync_standbys"][0]

    await check_writes(ops_test)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_stereo_sync_standby(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    await start_continuous_writes(ops_test, app)
    logger.info("Deleting sync replica")
    primary = await get_primary(ops_test, app)
    secondary = next(
        filter(lambda x: x.name != primary, ops_test.model.applications[DATABASE_APP_NAME].units)
    ).name
    await ops_test.model.destroy_unit(secondary, force=True, destroy_storage=False, max_wait=1500)

    await ops_test.model.wait_for_idle(status="active", timeout=600, idle_period=45)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await are_writes_increasing(ops_test, secondary)

    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert len(new_roles["sync_standbys"]) == 1
    assert new_roles["primaries"][0] == original_roles["primaries"][0]

    await check_writes(ops_test)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_scale_to_five_units(ops_test: OpsTest) -> None:
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=3)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_raft_majority(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )

    await start_continuous_writes(ops_test, app)
    logger.info("Deleting primary")
    await gather(
        ops_test.model.destroy_unit(
            original_roles["primaries"][0], force=True, destroy_storage=False, max_wait=1500
        ),
        ops_test.model.destroy_unit(
            original_roles["sync_standbys"][0], force=True, destroy_storage=False, max_wait=1500
        ),
        ops_test.model.destroy_unit(
            original_roles["sync_standbys"][1], force=True, destroy_storage=False, max_wait=1500
        ),
    )

    left_unit = ops_test.model.units[original_roles["sync_standbys"][2]]
    await ops_test.model.block_until(
        lambda: left_unit.workload_status == "blocked"
        and left_unit.workload_status_message == "Raft majority loss, run: promote-to-primary",
        timeout=600,
    )

    run_action = await left_unit.run_action("promote-to-primary", scope="unit", force=True)
    await run_action.wait()

    await ops_test.model.wait_for_idle(status="active", timeout=900, idle_period=45)

    await are_writes_increasing(
        ops_test,
        [
            original_roles["primaries"][0],
            original_roles["sync_standbys"][0],
            original_roles["sync_standbys"][1],
        ],
    )

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=3)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await check_writes(ops_test)
    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert len(new_roles["sync_standbys"]) == 4
    assert new_roles["primaries"][0] == original_roles["sync_standbys"][2]
