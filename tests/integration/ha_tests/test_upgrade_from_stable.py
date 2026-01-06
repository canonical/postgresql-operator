# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    count_switchovers,
    get_leader_unit,
    get_primary,
    remove_chown_workaround,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

TIMEOUT = 900


@pytest.mark.abort_on_fail
async def test_deploy_stable(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    return_code, charm_info, stderr = await ops_test.juju("info", "postgresql", "--format=json")
    if return_code != 0:
        raise Exception(f"failed to get charm info with error: {stderr}")
    # Revisions lower than 315 have a currently broken workaround for chown.
    parsed_charm_info = json.loads(charm_info)
    revision = (
        parsed_charm_info["channels"]["14"]["stable"][0]["revision"]
        if "channels" in parsed_charm_info
        else parsed_charm_info["channel-map"]["14/stable"]["revision"]
    )
    logger.info(f"14/stable revision: {revision}")
    if int(revision) < 315:
        original_charm_name = "./postgresql.charm"
        return_code, _, stderr = await ops_test.juju(
            "download",
            "postgresql",
            "--channel=14/stable",
            f"--filepath={original_charm_name}",
        )
        if return_code != 0:
            raise Exception(
                f"failed to download charm from 14/stable channel with error: {stderr}"
            )
        patched_charm_name = "./modified_postgresql.charm"
        remove_chown_workaround(original_charm_name, patched_charm_name)
        return_code, _, stderr = await ops_test.juju("deploy", patched_charm_name, "-n", "3")
        if return_code != 0:
            raise Exception(f"failed to deploy charm from 14/stable channel with error: {stderr}")
    else:
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            num_units=3,
            channel="14/stable",
        )
    await ops_test.model.deploy(
        APPLICATION_NAME,
        num_units=1,
        channel="latest/edge",
    )
    await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
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
    actions = await application.get_actions()

    logger.info("Refresh the charm")
    await application.refresh(path=charm)

    logger.info("Wait for upgrade to start")
    await ops_test.model.block_until(
        lambda: (
            ("waiting" if "pre-upgrade-check" in actions else "maintenance")
            in {unit.workload_status for unit in application.units}
        ),
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
        assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
            "Number of switchovers is greater than 2"
        )
