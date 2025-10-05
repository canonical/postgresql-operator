#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import exceptions, gather, sleep
from copy import deepcopy

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    get_machine_from_unit,
    stop_machine,
)
from .conftest import APPLICATION_NAME
from .helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    get_cluster_roles,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

charm = None


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
                num_units=3,
                base=CHARM_BASE,
                config={"profile": "testing", "synchronous_node_count": "majority"},
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


@pytest.mark.parametrize(
    "roles",
    [
        ["primaries"],
        ["sync_standbys"],
        ["replicas"],
        ["primaries", "replicas"],
        ["sync_standbys", "replicas"],
    ],
)
@pytest.mark.abort_on_fail
async def test_removing_unit(ops_test: OpsTest, roles: list[str], continuous_writes) -> None:
    logger.info(f"removing {', '.join(roles)}")
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    copied_roles = deepcopy(original_roles)
    await start_continuous_writes(ops_test, app)
    units = [copied_roles[role].pop(0) for role in roles]
    for unit in units:
        logger.info(f"Stopping unit {unit}")
        await stop_machine(ops_test, await get_machine_from_unit(ops_test, unit))
    await sleep(15)
    for unit in units:
        logger.info(f"Deleting unit {unit}")
        await ops_test.model.destroy_unit(unit, force=True, destroy_storage=False, max_wait=1500)

    if len(roles) > 1:
        for left_unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            if left_unit.name not in units:
                break
        try:
            await ops_test.model.block_until(
                lambda: left_unit.workload_status == "blocked"
                and left_unit.workload_status_message
                == "Raft majority loss, run: promote-to-primary",
                timeout=600,
            )
            await ops_test.model.wait_for_idle(timeout=600, idle_period=45)

            run_action = (
                await ops_test.model.applications[DATABASE_APP_NAME]
                .units[0]
                .run_action("promote-to-primary", scope="unit", force=True)
            )
            await run_action.wait()
        except exceptions.TimeoutError:
            # Check if Patroni self healed
            assert (
                left_unit.workload_status == "active"
                and left_unit.workload_status_message == "Primary"
            )
            logger.warning(f"Patroni self-healed without raft reinitialisation for roles {roles}")

    await ops_test.model.wait_for_idle(status="active", timeout=600, idle_period=45)

    await are_writes_increasing(ops_test, units)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=len(roles))
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert len(new_roles["sync_standbys"]) == 1
    assert len(new_roles["replicas"]) == 1
    if "primaries" in roles:
        assert new_roles["primaries"][0] in original_roles["sync_standbys"]
    else:
        assert new_roles["primaries"][0] == original_roles["primaries"][0]

    await check_writes(ops_test)
