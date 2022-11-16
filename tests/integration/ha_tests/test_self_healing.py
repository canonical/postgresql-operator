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
    change_loop_wait,
    change_master_start_timeout,
    count_writes,
    fetch_cluster_members,
    get_master_start_timeout,
    get_primary,
    is_replica,
    postgresql_ready,
    secondary_up_to_date,
    send_signal_to_process,
    start_continuous_writes,
    stop_continuous_writes,
    update_restart_delay,
)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/usr/local/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]


def pytest_generate_tests(metafunc):
    square_parameters = (x**2 for x in range(7))
    if "square" in metafunc.fixturenames:
        metafunc.parametrize("process", DB_PROCESSES)
    if "odd_square" in metafunc.fixturenames:
        odd_square_parameters = (x for x in square_parameters if x % 2 == 1)
        metafunc.parametrize("pause_cluster_management", odd_square_parameters)


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


@pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_kill_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Change the "master_start_timeout" parameter to speed up the fail-over.
    original_master_start_timeout = await get_master_start_timeout(ops_test)
    await change_master_start_timeout(ops_test, 0)

    # Kill the database process.
    await send_signal_to_process(ops_test, primary_name, process, kill_code="SIGKILL")

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

    # Revert the "master_start_timeout" parameter to avoid fail-over again.
    await change_master_start_timeout(ops_test, original_master_start_timeout)

    # Verify that the old primary is now a replica.
    assert is_replica(ops_test, primary_name), "there are more than one primary in the cluster."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

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
async def test_freeze_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Change the "master_start_timeout" parameter to speed up the fail-over.
    original_master_start_timeout = await get_master_start_timeout(ops_test)
    await change_master_start_timeout(ops_test, 0)

    # Freeze the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGSTOP")

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 3 minutes wait (this is a little more than the loop wait configuration, that is
        # considered to trigger a fail-over after master_start_timeout is changed, and also
        # when freezing the DB process it take some more time to trigger the fail-over).
        writes = await count_writes(ops_test, primary_name)
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test, primary_name)
                assert more_writes > writes, "writes not continuing to DB"

        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app)
                assert new_primary_name != primary_name

        # Revert the "master_start_timeout" parameter to avoid fail-over again.
        await change_master_start_timeout(ops_test, original_master_start_timeout)

        # Un-freeze the old primary.
        await send_signal_to_process(ops_test, primary_name, process, "SIGCONT")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that the old primary is now a replica.
    assert is_replica(ops_test, primary_name), "there are more than one primary in the cluster."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

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
async def test_restart_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Restart the database process.
    await send_signal_to_process(ops_test, primary_name, process, kill_code="SIGTERM")

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

    # Verify that the old primary is now a replica.
    assert is_replica(ops_test, primary_name), "there are more than one primary in the cluster."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

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
@pytest.mark.parametrize("process", [PATRONI_PROCESS])
async def test_full_cluster_restart(
    ops_test: OpsTest, process: str, continuous_writes, reset_restart_delay
) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # await pause_cluster_management(ops_test, ops_test.model.applications[app].units[0].name)
    await change_loop_wait(ops_test, 30)

    # update all units to have a new RESTART_DELAY,  Modifying the Restart delay to 3 minutes
    # should ensure enough time for all replicas to be down at the same time.
    for unit in ops_test.model.applications[app].units:
        await update_restart_delay(ops_test, unit, RESTART_DELAY)

    # Restart all units "simultaneously".
    await asyncio.gather(
        *[
            send_signal_to_process(ops_test, unit.name, process, kill_code="SIGTERM")
            for unit in ops_test.model.applications[app].units
        ]
    )

    # This test serves to verify behavior when all replicas are down at the same time that when
    # they come back online they operate as expected. This check verifies that we meet the criteria
    # of all replicas being down at the same time.
    assert await all_db_processes_down(ops_test, process), "Not all units down at the same time."
    await change_loop_wait(ops_test, 20)

    #     await resume_cluster_management(ops_test, ops_test.model.applications[app].units[0].name)

    # Verify all units are up and running.
    for unit in ops_test.model.applications[app].units:
        assert await postgresql_ready(
            ops_test, unit.name
        ), f"unit {unit.name} not restarted after cluster crash."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    writes = await count_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
        with attempt:
            more_writes = await count_writes(ops_test)
            assert more_writes > writes, "writes not continuing to DB"

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."


@pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", [PATRONI_PROCESS])
async def test_full_cluster_crash(
    ops_test: OpsTest, process: str, continuous_writes, reset_restart_delay
) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # await pause_cluster_management(ops_test, ops_test.model.applications[app].units[0].name)
    await change_loop_wait(ops_test, 20)

    # update all units to have a new RESTART_DELAY,  Modifying the Restart delay to 3 minutes
    # should ensure enough time for all replicas to be down at the same time.
    for unit in ops_test.model.applications[app].units:
        await update_restart_delay(ops_test, unit, RESTART_DELAY)

    # Restart all units "simultaneously".
    await asyncio.gather(
        *[
            send_signal_to_process(ops_test, unit.name, process, kill_code="SIGKILL")
            for unit in ops_test.model.applications[app].units
        ]
    )

    # This test serves to verify behavior when all replicas are down at the same time that when
    # they come back online they operate as expected. This check verifies that we meet the criteria
    # of all replicas being down at the same time.
    assert await all_db_processes_down(ops_test, process), "Not all units down at the same time."
    await change_loop_wait(ops_test, 10)

    # await resume_cluster_management(ops_test, ops_test.model.applications[app].units[0].name)

    # Verify all units are up and running.
    for unit in ops_test.model.applications[app].units:
        assert await postgresql_ready(
            ops_test, unit.name
        ), f"unit {unit.name} not restarted after cluster crash."

    writes = await count_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
        with attempt:
            more_writes = await count_writes(ops_test)
            assert more_writes > writes, "writes not continuing to DB"

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."
