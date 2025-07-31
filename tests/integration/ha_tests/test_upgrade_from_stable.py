# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    count_switchovers,
    get_leader_unit,
    get_primary,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

TIMEOUT = 25 * 60


@pytest.mark.abort_on_fail
async def test_deploy_stable(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    await gather(
        ops_test.model.deploy(
            DATABASE_APP_NAME, num_units=3, channel="16/stable", config={"profile": "testing"}
        ),
        ops_test.model.deploy(
            APPLICATION_NAME,
            num_units=1,
            channel="latest/edge",
            config={"sleep_interval": 500},
        ),
    )
    await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
    logger.info("Wait for applications to become active")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, APPLICATION_NAME], status="active", timeout=(20 * 60)
        )
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3


@pytest.mark.abort_on_fail
async def test_pre_refresh_check(ops_test: OpsTest) -> None:
    """Test that the pre-refresh-check action runs successfully."""
    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"

    logger.info("Run pre-refresh-check action")
    action = await leader_unit.run_action("pre-refresh-check")
    await action.wait()


@pytest.mark.abort_on_fail
async def test_upgrade_from_stable(ops_test: OpsTest, charm):
    """Test updating from stable channel."""
    # Start an application that continuously writes data to the database.
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    primary_name = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    initial_number_of_switchovers = count_switchovers(ops_test, primary_name)

    application = ops_test.model.applications[DATABASE_APP_NAME]

    logger.info("Refresh the charm")
    await application.refresh(path=charm)

    logger.info("Wait for upgrade to start")
    try:
        # Blocked status is expected due to:
        # (on PR) compatibility checks (on PR charm revision is '16/1.25.0+dirty...')
        # (non-PR) the first unit upgraded and paused (pause_after_unit_refresh=first)
        await ops_test.model.block_until(lambda: application.status == "blocked", timeout=60 * 3)

        logger.info("Wait for refresh to block as paused or incompatible")
        async with ops_test.fast_forward("60s"):
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
            )

        # Highest to lowest unit number
        refresh_order = sorted(
            application.units, key=lambda unit: int(unit.name.split("/")[1]), reverse=True
        )

        if "Refresh incompatible" in application.status_message:
            logger.info("Application refresh is blocked due to incompatibility")

            action = await refresh_order[0].run_action(
                "force-refresh-start", **{"check-compatibility": False}
            )
            await action.wait()

            logger.info("Wait for first incompatible unit to upgrade")
            async with ops_test.fast_forward("60s"):
                await ops_test.model.wait_for_idle(
                    apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
                )

        logger.info("Run resume-refresh action")
        action = await refresh_order[1].run_action("resume-refresh")
        await action.wait()
    except TimeoutError:
        # If the application didn't get into the blocked state, it should have upgraded only
        # the charm code because the snap revision didn't change.
        logger.info("Upgrade completed without snap refresh (charm.py upgrade only)")
        assert application.status == "active", (
            "Application didn't reach blocked or active state after refresh attempt"
        )

    logger.info("Wait for upgrade to complete")
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", idle_period=30, timeout=TIMEOUT
        )

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)

    logger.info("checking the number of switchovers")
    final_number_of_switchovers = count_switchovers(ops_test, primary_name)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
        "Number of switchovers is greater than 2"
    )
