#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    METADATA,
    RESTART_DELAY,
    all_db_processes_down,
    app_name,
    count_writes,
    get_primary,
    kill_process,
    postgresql_ready,
    secondary_up_to_date,
    start_continuous_writes,
    stop_continuous_writes,
    update_restart_delay,
)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/usr/local/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]


@pytest.mark.abort_on_fail
@pytest.mark.ha_self_healing_tests
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if await app_name(ops_test):
        return

    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(charm, resources={"patroni": "patroni.tar.gz"}, num_units=3)
        await ops_test.juju("attach-resource", APP_NAME, "patroni=patroni.tar.gz")
        await ops_test.model.wait_for_idle(status="active", timeout=1000)


# @pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_kill_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Kill the database process.
    await kill_process(ops_test, primary_name, process, kill_code="SIGKILL")

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 60 seconds wait (this is a little more than the loop wait configuration, that is
        # considered to trigger a fail-over after master_start_timeout is changed).
        writes = await count_writes(ops_test)
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test)
                assert more_writes > writes, "writes not continuing to DB"

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app)
    assert new_primary_name != primary_name

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."


# @pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_restart_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Restart the database process.
    await kill_process(ops_test, primary_name, process, kill_code="SIGTERM")

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 60 seconds wait (this is a little more than the loop wait configuration, that is
        # considered to trigger a fail-over after master_start_timeout is changed).
        writes = await count_writes(ops_test)
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test)
                assert more_writes > writes, "writes not continuing to DB"

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app)
    assert new_primary_name != primary_name

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."


@pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_full_cluster_restart(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout, reset_restart_delay
) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # update all units to have a new RESTART_DELAY,  Modifying the Restart delay to 3 minutes
    # should ensure enough time for all replicas to be down at the same time.
    for unit in ops_test.model.applications[app].units:
        await update_restart_delay(ops_test, unit, RESTART_DELAY)

    # Restart all units "simultaneously".
    await asyncio.gather(
        *[
            kill_process(ops_test, unit.name, process, kill_code="SIGTERM")
            for unit in ops_test.model.applications[app].units
        ]
    )

    # This test serves to verify behavior when all replicas are down at the same time that when
    # they come back online they operate as expected. This check verifies that we meet the criteria
    # of all replicas being down at the same time.
    assert await all_db_processes_down(ops_test, process), "Not all units down at the same time."

    # # Verify all units are up and running.
    # for unit in ops_test.model.applications[app].units:
    #     assert await postgresql_ready(
    #         ops_test, unit.public_address
    #     ), f"unit {unit.name} not restarted after cluster crash."
