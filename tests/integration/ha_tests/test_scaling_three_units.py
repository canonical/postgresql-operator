#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather, sleep

import pytest
from pytest_operator.plugin import OpsTest

from .. import markers
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


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy two PostgreSQL clusters."""
    # This is a potentially destructive test, so it shouldn't be run against existing clusters
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        # Deploy the first cluster with reusable storage
        await gather(
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=3,
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

        await ops_test.model.wait_for_idle(status="active", timeout=1500)


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.parametrize("role", ["primaries", "sync_standbys", "replicas"])
@pytest.mark.abort_on_fail
async def test_removing_single_unit(ops_test: OpsTest, role: str, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    await start_continuous_writes(ops_test, app)
    logger.info("Stopping unit")
    unit = original_roles[role][0]
    await stop_machine(ops_test, await get_machine_from_unit(ops_test, unit))
    await sleep(15)
    logger.info("Deleting unit")
    await ops_test.model.destroy_unit(unit, force=True, destroy_storage=False, max_wait=1500)

    await ops_test.model.wait_for_idle(status="active", timeout=600, idle_period=45)

    await are_writes_increasing(ops_test, unit)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert len(new_roles["sync_standbys"]) == 1
    assert len(new_roles["replicas"]) == 1
    if role == "primaries":
        assert new_roles["primaries"][0] == original_roles["sync_standbys"][0]
    else:
        assert new_roles["primaries"][0] == original_roles["primaries"][0]

    await check_writes(ops_test)
