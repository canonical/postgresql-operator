#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import subprocess
from asyncio import gather

import pytest
from juju.model import Model
from pytest_operator.plugin import OpsTest

from .. import markers
from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    get_leader_unit,
    get_machine_from_unit,
)
from .conftest import fast_forward
from .helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    cut_network_from_unit,
    get_ip_from_inside_the_unit,
    get_unit_ip,
    is_postgresql_ready,
    restore_network_for_unit,
    start_continuous_writes,
    wait_network_restore,
)

logger = logging.getLogger(__name__)

CLUSTER_SIZE = 3
FAST_INTERVAL = "10s"
IDLE_PERIOD = 5
TIMEOUT = 2000


@markers.juju3
@pytest.mark.abort_on_fail
async def test_deploy_async_replication_setup(
    ops_test: OpsTest, first_model: Model, second_model: Model, charm
) -> None:
    """Deploy two PostgreSQL clusters with async replication and continuous writes app."""
    if not await app_name(ops_test):
        await ops_test.model.deploy(
            charm,
            num_units=CLUSTER_SIZE,
            config={"profile": "testing"},
        )
    if not await app_name(ops_test, model=second_model):
        await second_model.deploy(
            charm,
            num_units=CLUSTER_SIZE,
            config={"profile": "testing"},
        )
    await ops_test.model.deploy(
        APPLICATION_NAME,
        channel="latest/edge",
        num_units=1,
        series="jammy",
        config={"sleep_interval": 1000},
    )

    async with ops_test.fast_forward(), fast_forward(second_model):
        await first_model.wait_for_idle(apps=[APPLICATION_NAME], status="blocked")
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME],
                status="active",
                timeout=TIMEOUT,
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME],
                status="active",
                timeout=TIMEOUT,
            ),
        )


@markers.juju3
@pytest.mark.abort_on_fail
async def test_establish_async_replication(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
) -> None:
    """Set up async replication between the two clusters."""
    first_offer_command = f"offer {DATABASE_APP_NAME}:replication-offer replication-offer"
    await ops_test.juju(*first_offer_command.split())
    first_consume_command = (
        f"consume -m {second_model.info.name} admin/{first_model.info.name}.replication-offer"
    )
    await ops_test.juju(*first_consume_command.split())

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    await second_model.relate(DATABASE_APP_NAME, "replication-offer")

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    # Promote the first cluster as the primary.
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"
    run_action = await leader_unit.run_action("create-replication")
    await run_action.wait()
    assert (run_action.results.get("return-code", None) == 0) or (
        run_action.results.get("Code", None) == "0"
    ), "Promote action failed"

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )


@markers.juju3
@markers.amd64_only
@pytest.mark.abort_on_fail
async def test_ip_change_during_async_replication(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
    continuous_writes,
) -> None:
    """Test that async replication survives an IP change on a primary cluster unit."""
    logger.info("starting continuous writes to the database")
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test, extra_model=second_model)

    # Pick a unit from the primary cluster to cut network from.
    unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
    unit_name = unit.name
    unit_hostname = await get_machine_from_unit(ops_test, unit_name)
    old_ip = await get_unit_ip(ops_test, unit_name)
    logger.info(f"Cutting network for {unit_name} (hostname={unit_hostname}, ip={old_ip})")

    cut_network_from_unit(unit_hostname)

    # Release the DHCP lease inside the container so it gets a new IP on restore.
    # The eth0 device is masked (type=none), but we can still exec into the container.
    subprocess.run(
        f"lxc exec {unit_hostname} -- dhclient -r eth0".split(),
        capture_output=True,
    )

    async with ops_test.fast_forward():
        logger.info("checking whether writes are increasing (excluding the cut unit)")
        await are_writes_increasing(ops_test, down_unit=unit_name, extra_model=second_model)

    logger.info(f"Restoring network for {unit_name}")
    restore_network_for_unit(unit_hostname)

    # Wait until the cluster becomes idle after IP update.
    logger.info("waiting for cluster to become idle after restoring network")
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=TIMEOUT,
            idle_period=30,
        )

    # Wait for the unit to get its new IP.
    logger.info("waiting for IP address to change on the unit")
    await wait_network_restore(ops_test, unit_name, old_ip)
    new_ip = await get_ip_from_inside_the_unit(ops_test, unit_name)
    logger.info(f"IP changed from {old_ip} to {new_ip}")
    assert new_ip != old_ip, f"IP did not change after network restore ({old_ip})"

    # Verify that the database service is ready on the unit with the new IP.
    logger.info(f"waiting for the database service to be ready on {unit_name}")
    assert await is_postgresql_ready(ops_test, unit_name, use_ip_from_inside=True)

    # Wait for both clusters to settle after the IP change propagation.
    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    # Verify that writes are still replicating to both clusters after the IP change.
    logger.info("checking whether writes are increasing on both clusters after IP change")
    await are_writes_increasing(ops_test, use_ip_from_inside=True, extra_model=second_model)

    # Stop writes and verify no data was lost across both clusters.
    logger.info("checking whether no writes were lost across both clusters")
    await check_writes(ops_test, use_ip_from_inside=True, extra_model=second_model)
