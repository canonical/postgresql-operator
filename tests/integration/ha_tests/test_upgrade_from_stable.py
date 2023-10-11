# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.helpers import (
    APPLICATION_NAME,
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    count_switchovers,
    get_leader_unit,
    get_primary,
)

logger = logging.getLogger(__name__)

TIMEOUT = 5 * 60


@pytest.mark.abort_on_fail
async def test_deploy_stable(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    await ops_test.model.deploy(
        DATABASE_APP_NAME,
        num_units=3,
        channel="14/stable",
        trust=True,
    ),
    await ops_test.model.deploy(
        APPLICATION_NAME,
        num_units=1,
        channel="latest/edge",
    )
    logger.info("Wait for applications to become active")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, APPLICATION_NAME], status="active", timeout=(20 * 60)
        )
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3


@pytest.mark.abort_on_fail
async def test_pre_upgrade_check(ops_test: OpsTest) -> None:
    """Test that the pre-upgrade-check action runs successfully."""
    application = ops_test.model.applications[DATABASE_APP_NAME]
    if "pre-upgrade-check" not in await application.get_actions():
        logger.info("skipping the test because the charm from 14/stable doesn't support upgrade")
        return

    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"

    logger.info("Run pre-upgrade-check action")
    action = await leader_unit.run_action("pre-upgrade-check")
    await action.wait()


@pytest.mark.abort_on_fail
async def test_upgrade_from_stable(ops_test: OpsTest):
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
    actions = await application.get_actions()

    logger.info("Build charm locally")
    charm = await ops_test.build_charm(".")

    logger.info("Refresh the charm")
    await application.refresh(path=charm)

    logger.info("Wait for upgrade to start")
    await ops_test.model.block_until(
        lambda: ("waiting" if "pre-upgrade-check" in actions else "maintenance")
        in {unit.workload_status for unit in application.units},
        timeout=TIMEOUT,
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

    # Check the number of switchovers.
    if "pre-upgrade-check" in actions:
        logger.info("checking the number of switchovers")
        final_number_of_switchovers = count_switchovers(ops_test, primary_name)
        assert (
            final_number_of_switchovers - initial_number_of_switchovers
        ) <= 2, "Number of switchovers is greater than 2"
