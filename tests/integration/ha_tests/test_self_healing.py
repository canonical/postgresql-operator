#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    METADATA,
    ORIGINAL_RESTART_CONDITION,
    app_name,
    are_all_db_processes_down,
    are_writes_increasing,
    change_patroni_setting,
    change_wal_settings,
    check_writes,
    fetch_cluster_members,
    get_patroni_setting,
    get_primary,
    is_cluster_updated,
    is_postgresql_ready,
    list_wal_files,
    send_signal_to_process,
    start_continuous_writes,
    update_restart_condition,
)
from tests.integration.helpers import (
    CHARM_SERIES,
    db_connect,
    get_password,
    get_unit_address,
    run_command_on_unit,
)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        charm = await ops_test.build_charm(".")
        async with ops_test.fast_forward():
            await ops_test.model.deploy(charm, num_units=3, series=CHARM_SERIES)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(
                charm, application_name=APPLICATION_NAME, series=CHARM_SERIES
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_kill_db_process(
    ops_test: OpsTest, process: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Kill the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGKILL")

    async with ops_test.fast_forward():
        await are_writes_increasing(ops_test, primary_name)

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app)
    assert new_primary_name != primary_name

    await is_cluster_updated(ops_test, primary_name)


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
                    new_primary_name = await get_primary(ops_test, app)
                    assert new_primary_name != primary_name
        finally:
            # Un-freeze the old primary.
            await send_signal_to_process(ops_test, primary_name, process, "SIGCONT")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    await is_cluster_updated(ops_test, primary_name)


@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_restart_db_process(
    ops_test: OpsTest, process: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Restart the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGTERM")

    async with ops_test.fast_forward():
        await are_writes_increasing(ops_test, primary_name)

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app)
    assert new_primary_name != primary_name

    await is_cluster_updated(ops_test, primary_name)


@pytest.mark.parametrize("process", DB_PROCESSES)
@pytest.mark.parametrize("signal", ["SIGTERM", "SIGKILL"])
async def test_full_cluster_restart(
    ops_test: OpsTest, process: str, signal: str, continuous_writes, reset_restart_condition
) -> None:
    """This tests checks that a cluster recovers from a full cluster restart.

    The test can be called a full cluster crash when the signal sent to the OS process
    is SIGKILL.
    """
    # Locate primary unit.
    app = await app_name(ops_test)

    # Change the loop wait setting to make Patroni wait more time before restarting PostgreSQL.
    initial_loop_wait = await get_patroni_setting(ops_test, "loop_wait")
    await change_patroni_setting(ops_test, "loop_wait", 300)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Restart all units "simultaneously".
    await asyncio.gather(
        *[
            send_signal_to_process(ops_test, unit.name, process, signal)
            for unit in ops_test.model.applications[app].units
        ]
    )

    # This test serves to verify behavior when all replicas are down at the same time that when
    # they come back online they operate as expected. This check verifies that we meet the criteria
    # of all replicas being down at the same time.
    try:
        assert await are_all_db_processes_down(
            ops_test, process
        ), "Not all units down at the same time."
    finally:
        if process == PATRONI_PROCESS:
            awaits = []
            for unit in ops_test.model.applications[app].units:
                awaits.append(update_restart_condition(ops_test, unit, ORIGINAL_RESTART_CONDITION))
            await asyncio.gather(*awaits)
        await change_patroni_setting(ops_test, "loop_wait", initial_loop_wait)

    # Verify all units are up and running.
    for unit in ops_test.model.applications[app].units:
        assert await is_postgresql_ready(
            ops_test, unit.name
        ), f"unit {unit.name} not restarted after cluster restart."

    await are_writes_increasing(ops_test)

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [unit.public_address for unit in ops_test.model.applications[app].units]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    await check_writes(ops_test)


@pytest.mark.unstable
async def test_forceful_restart_without_data_and_transaction_logs(
    ops_test: OpsTest,
    continuous_writes,
    primary_start_timeout,
    wal_settings,
) -> None:
    """A forceful restart with deleted data and without transaction logs (forced clone)."""
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Copy data dir content removal script.
    await ops_test.juju(
        "scp", "tests/integration/ha_tests/clean-data-dir.sh", f"{primary_name}:/tmp"
    )

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Stop the systemd service on the primary unit.
    await run_command_on_unit(ops_test, primary_name, "snap stop charmed-postgresql.patroni")

    # Data removal runs within a script, so it allows `*` expansion.
    return_code, _, _ = await ops_test.juju(
        "ssh",
        primary_name,
        "sudo",
        "/tmp/clean-data-dir.sh",
    )
    assert return_code == 0, "Failed to remove data directory"

    async with ops_test.fast_forward():
        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app)
                assert new_primary_name is not None
                assert new_primary_name != primary_name

        await are_writes_increasing(ops_test, primary_name)

        # Change some settings to enable WAL rotation.
        for unit in ops_test.model.applications[app].units:
            if unit.name == primary_name:
                continue
            await change_wal_settings(ops_test, unit.name, 32, 32, 1)

        # Rotate the WAL segments.
        files = await list_wal_files(ops_test, app)
        host = get_unit_address(ops_test, new_primary_name)
        password = await get_password(ops_test, new_primary_name)
        with db_connect(host, password) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                # Run some commands to make PostgreSQL do WAL rotation.
                cursor.execute("SELECT pg_switch_wal();")
                cursor.execute("CHECKPOINT;")
                cursor.execute("SELECT pg_switch_wal();")
        connection.close()
        new_files = await list_wal_files(ops_test, app)
        # Check that the WAL was correctly rotated.
        for unit_name in files:
            assert not files[unit_name].intersection(
                new_files
            ), "WAL segments weren't correctly rotated"

        # Start the systemd service in the old primary.
        await run_command_on_unit(ops_test, primary_name, "snap start charmed-postgresql.patroni")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    await is_cluster_updated(ops_test, primary_name)
