#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import CHARM_BASE, DATABASE_APP_NAME, get_password
from .conftest import APPLICATION_NAME
from .helpers import (
    METADATA,
    ORIGINAL_RESTART_CONDITION,
    app_name,
    are_all_db_processes_down,
    are_writes_increasing,
    change_patroni_setting,
    check_writes,
    fetch_cluster_members,
    get_patroni_setting,
    get_primary,
    is_cluster_updated,
    is_postgresql_ready,
    send_signal_to_process,
    start_continuous_writes,
    update_restart_condition,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/snap/charmed-postgresql/[0-9]*/usr/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]
MEDIAN_ELECTION_TIME = 10


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                charm,
                num_units=3,
                base=CHARM_BASE,
                storage={
                    "archive": {"pool": "lxd-btrfs", "size": 2048},
                    "data": {"pool": "lxd-btrfs", "size": 2048},
                    "logs": {"pool": "lxd-btrfs", "size": 2048},
                    "temp": {"pool": "lxd-btrfs", "size": 2048},
                },
                config={"profile": "testing"},
            )
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                base=CHARM_BASE,
                channel="edge",
            )

    if wait_for_apps:
        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1500)


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_freeze_db_process(
    ops_test: OpsTest, process: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Freeze the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGSTOP")

    # Wait some time to elect a new primary.
    sleep(MEDIAN_ELECTION_TIME * 6)

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 3 minutes wait (this is a little more than the loop wait configuration, that is
        # considered to trigger a fail-over after primary_start_timeout is changed, and also
        # when freezing the DB process it take some more time to trigger the fail-over).
        try:
            await are_writes_increasing(ops_test, primary_name)

            # Verify that a new primary gets elected (ie old primary is secondary).
            for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
                with attempt:
                    new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
                    assert new_primary_name != primary_name
        finally:
            # Un-freeze the old primary.
            await send_signal_to_process(ops_test, primary_name, process, "SIGCONT")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    await is_cluster_updated(ops_test, primary_name)


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("process", DB_PROCESSES)
@pytest.mark.parametrize("signal", ["SIGTERM", "SIGKILL"])
async def test_full_cluster_restart(
    ops_test: OpsTest,
    process: str,
    signal: str,
    continuous_writes,
    reset_restart_condition,
    loop_wait,
) -> None:
    """This tests checks that a cluster recovers from a full cluster restart.

    The test can be called a full cluster crash when the signal sent to the OS process
    is SIGKILL.
    """
    # Locate primary unit.
    app = await app_name(ops_test)
    patroni_password = await get_password(ops_test, "patroni")

    # Change the loop wait setting to make Patroni wait more time before restarting PostgreSQL.
    initial_loop_wait = await get_patroni_setting(ops_test, "loop_wait")
    initial_ttl = await get_patroni_setting(ops_test, "ttl")
    # loop_wait parameter is limited by ttl value, thus we should increase it first
    await change_patroni_setting(ops_test, "ttl", 600, patroni_password, use_random_unit=True)
    await change_patroni_setting(
        ops_test, "loop_wait", 300, patroni_password, use_random_unit=True
    )

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Restart all units "simultaneously".
    await asyncio.gather(*[
        send_signal_to_process(ops_test, unit.name, process, signal)
        for unit in ops_test.model.applications[app].units
    ])

    # This test serves to verify behavior when all replicas are down at the same time that when
    # they come back online they operate as expected. This check verifies that we meet the criteria
    # of all replicas being down at the same time.
    try:
        assert await are_all_db_processes_down(ops_test, process, signal), (
            "Not all units down at the same time."
        )
    finally:
        if process == PATRONI_PROCESS:
            awaits = []
            for unit in ops_test.model.applications[app].units:
                awaits.append(update_restart_condition(ops_test, unit, ORIGINAL_RESTART_CONDITION))
            await asyncio.gather(*awaits)
        await change_patroni_setting(
            ops_test, "loop_wait", initial_loop_wait, patroni_password, use_random_unit=True
        )
        await change_patroni_setting(
            ops_test, "ttl", initial_ttl, patroni_password, use_random_unit=True
        )

    # Verify all units are up and running.
    sleep(30)
    for unit in ops_test.model.applications[app].units:
        assert await is_postgresql_ready(ops_test, unit.name), (
            f"unit {unit.name} not restarted after cluster restart."
        )

    # Check if a primary is elected
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
        with attempt:
            new_primary_name = await get_primary(ops_test, app)
            assert new_primary_name is not None, "Could not get primary from any unit"

    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
        await are_writes_increasing(ops_test)

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    async with ops_test.fast_forward():
        await check_writes(ops_test)
