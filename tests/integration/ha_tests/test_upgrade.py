#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import subprocess
import zipfile

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)
from tests.integration.helpers import get_primary

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # Check if there is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        charm = await ops_test.build_charm(".")
        async with ops_test.fast_forward():
            await ops_test.model.deploy(charm, num_units=3)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(charm, application_name=APPLICATION_NAME)

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


async def test_successful_upgrade(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    logger.info("starting continuous writes to the database")
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    any_unit_name = next(iter(ops_test.model.applications[app].units)).name
    primary_name = await get_primary(ops_test, any_unit_name)
    await are_writes_increasing(ops_test, primary_name)

    # Run the pre-upgrade-check action.
    logger.info("running pre-upgrade check")
    leader_unit_name = None
    for unit in ops_test.model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit_name = unit.name
            break
    action = await ops_test.model.units.get(leader_unit_name).run_action("pre-upgrade-check")
    await action.wait()
    assert action.results["Code"] == "0"

    # Run juju refresh.
    logger.info("refreshing the charm")
    application = ops_test.model.applications[app]
    charm = await ops_test.build_charm(".")
    await application.refresh(path=charm)
    async with ops_test.fast_forward(fast_interval="30s"):
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", idle_period=15, raise_on_blocked=True
        )

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    primary_name = await get_primary(ops_test, any_unit_name)
    await are_writes_increasing(ops_test, primary_name)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)


async def test_failed_upgrade(ops_test: OpsTest) -> None:
    # Run the pre-upgrade-check action.
    logger.info("running pre-upgrade check")
    app = await app_name(ops_test)
    leader_unit_name = None
    for unit in ops_test.model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit_name = unit.name
            break
    action = await ops_test.model.units.get(leader_unit_name).run_action("pre-upgrade-check")
    await action.wait()
    assert action.results["Code"] == "0"

    # Run juju refresh.
    logger.info("refreshing the charm")
    charm = await ops_test.build_charm(".")
    print(f"charm: {charm}")
    modified_charm = f"{charm}.modified"
    print(f"modified_charm: {modified_charm}")
    with zipfile.ZipFile(charm, "r") as charm_file, zipfile.ZipFile(
        modified_charm, "w"
    ) as modified_charm_file:
        # Iterate the input files
        unix_attributes = {}
        for charm_info in charm_file.infolist():
            # Read input file
            with charm_file.open(charm_info) as file:
                print(f"charm_info.filename: {charm_info.filename}")
                if charm_info.filename == "src/dependency.json":
                    content = json.loads(file.read())
                    # Modify the content of the file by replacing a string
                    content["snap"]["upgrade_supported"] = "^15"
                    content["snap"]["version"] = "15.1"
                    # Write content.
                    modified_charm_file.writestr(charm_info.filename, json.dumps(content))
                else:  # Other file, don't want to modify => just copy it.
                    content = file.read()
                    modified_charm_file.writestr(charm_info.filename, content)
                unix_attributes[charm_info.filename] = charm_info.external_attr >> 16

        for modified_charm_info in modified_charm_file.infolist():
            modified_charm_info.external_attr = unix_attributes[modified_charm_info.filename] << 16
    process = subprocess.run(
        f"juju refresh --model {ops_test.model.info.name} {app} --path {modified_charm}".split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise Exception(
            f"Expected juju refresh command to succeed instead it failed: {process.returncode} - {process.stderr.decode()}"
        )
    async with ops_test.fast_forward(fast_interval="30s"):
        await ops_test.model.wait_for_idle(
            apps=[app], status="blocked", idle_period=15, raise_on_blocked=False
        )


async def test_rollback(ops_test: OpsTest) -> None:
    # Run the pre-upgrade-check action.
    logger.info("running pre-upgrade check")
    app = await app_name(ops_test)
    leader_unit_name = None
    for unit in ops_test.model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit_name = unit.name
            break
    action = await ops_test.model.units.get(leader_unit_name).run_action("pre-upgrade-check")
    await action.wait()
    assert action.results["Code"] == "0"

    # Run juju refresh.
    logger.info("refreshing the charm")
    application = ops_test.model.applications[app]
    charm = await ops_test.build_charm(".")
    await application.refresh(path=charm)
    async with ops_test.fast_forward(fast_interval="30s"):
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", idle_period=15, raise_on_blocked=True
        )
