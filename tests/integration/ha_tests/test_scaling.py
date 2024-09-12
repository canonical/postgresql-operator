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
from .conftest import APPLICATION_NAME
from .helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    get_cluster_roles,
    get_primary,
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

        await ops_test.model.wait_for_idle(status="active", timeout=1500)


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_stereo_primary(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)
    logger.info("Deleting primary")
    primary = await get_primary(ops_test, app)
    await ops_test.model.destroy_unit(primary, force=True, destroy_storage=False, max_wait=1500)

    await ops_test.model.wait_for_idle(status="active", timeout=600)

    await are_writes_increasing(ops_test, primary)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await check_writes(ops_test)


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_stereo_sync_standby(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)
    logger.info("Deleting sync replica")
    primary = await get_primary(ops_test, app)
    secondary = next(
        filter(lambda x: x.name != primary, ops_test.model.applications[DATABASE_APP_NAME].units)
    ).name
    await ops_test.model.destroy_unit(secondary, force=True, destroy_storage=False, max_wait=1500)

    await ops_test.model.wait_for_idle(status="active", timeout=600)

    await are_writes_increasing(ops_test, secondary)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await check_writes(ops_test)


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_deploy_quatro(ops_test: OpsTest) -> None:
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=2)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_quatro_primary_and_async_replica(
    ops_test: OpsTest, continuous_writes
) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    roles = get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    await start_continuous_writes(ops_test, app)
    logger.info("Deleting primary")
    await gather(
        ops_test.model.destroy_unit(
            roles["primary"], force=True, destroy_storage=False, max_wait=1500
        ),
        await ops_test.model.destroy_unit(
            roles["replicas"][0], force=True, destroy_storage=False, max_wait=1500
        ),
    )

    await ops_test.model.wait_for_idle(status="active", timeout=600)

    await are_writes_increasing(ops_test, roles["primary"])

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=2)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await check_writes(ops_test)


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_quatro_sync_and_async_replica(
    ops_test: OpsTest, continuous_writes
) -> None:
    pass


@pytest.mark.group(1)
@markers.juju3
@pytest.mark.abort_on_fail
async def test_removing_quatro_both_async_replica(ops_test: OpsTest, continuous_writes) -> None:
    pass
