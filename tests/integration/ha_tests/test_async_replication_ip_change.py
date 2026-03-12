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
)
from .conftest import fast_forward
from .helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    cut_network_from_unit,
    get_standby_leader,
    get_unit_ip,
    restore_network_for_unit,
    start_continuous_writes,
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
    """Test that async replication survives an IP change on the standby cluster.

    The standby leader is the unit that connects to the primary cluster for
    streaming replication. Its IP must be allowed in the primary's pg_hba.conf.
    This test verifies that when the standby leader's IP changes, the primary
    cluster updates its pg_hba rules and replication continues without data loss.
    """
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Find the standby leader — the unit that connects to the primary for replication.
    standby_leader_member = await get_standby_leader(second_model, DATABASE_APP_NAME)
    assert standby_leader_member is not None, "No standby leader found"
    parts = standby_leader_member.rsplit("-", 1)
    standby_leader_name = f"{parts[0]}/{parts[1]}"
    logger.info(f"Standby leader: {standby_leader_name}")

    # Get hostname via juju exec (helpers don't support second model).
    result = subprocess.run(
        f"juju exec -m {second_model.info.name} --unit {standby_leader_name} -- hostname".split(),
        capture_output=True,
        text=True,
        check=True,
    )
    unit_hostname = result.stdout.strip()
    old_ip = await get_unit_ip(ops_test, standby_leader_name, model=second_model)
    logger.info(
        f"Cutting network for {standby_leader_name} (hostname={unit_hostname}, ip={old_ip})"
    )

    cut_network_from_unit(unit_hostname)

    # Release the DHCP lease so the unit gets a new IP on restore.
    subprocess.run(
        f"lxc exec {unit_hostname} -- dhclient -r eth0".split(),
        capture_output=True,
    )

    # Verify the primary cluster still accepts writes while standby is down.
    async with ops_test.fast_forward(), fast_forward(second_model):
        await are_writes_increasing(ops_test)

    restore_network_for_unit(unit_hostname)

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    new_ip = await get_unit_ip(ops_test, standby_leader_name, model=second_model)
    # Fall back to hostname -I if Juju still reports the old IP.
    if new_ip == old_ip:
        result = subprocess.run(
            f"juju exec -m {second_model.info.name} --unit {standby_leader_name} -- hostname -I".split(),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            new_ip = result.stdout.strip().split()[0]
    logger.info(f"IP changed from {old_ip} to {new_ip}")
    assert new_ip != old_ip, f"IP did not change after network disruption ({old_ip})"

    logger.info("checking whether writes are increasing on both clusters after IP change")
    await are_writes_increasing(ops_test)

    logger.info("checking whether no writes were lost across both clusters")
    await check_writes(ops_test, extra_model=second_model)
