#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    METADATA,
    RESTART_DELAY,
    app_name,
    change_master_start_timeout,
    change_wal_settings,
    count_writes,
    fetch_cluster_members,
    get_master_start_timeout,
    get_primary,
    is_replica,
    list_wal_files,
    postgresql_ready,
    secondary_up_to_date,
    send_signal_to_process,
    start_continuous_writes,
    stop_continuous_writes,
    update_restart_delay,
)
from tests.integration.helpers import db_connect, get_password, get_unit_address

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
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
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


# @pytest.mark.ha_self_healing_tests
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
@pytest.mark.parametrize("process", [POSTGRESQL_PROCESS])
async def test_sst(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout, reset_restart_delay
) -> None:
    """The SST test.

    A forceful restart instance with deleted data and without transaction logs (forced clone).
    """
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Copy data dir content removal script.
    await ops_test.juju(
        "scp", "tests/integration/ha_tests/clean-data-dir.sh", f"{primary_name}:/tmp"
    )

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Change the "master_start_timeout" parameter to speed up the fail-over.
    original_master_start_timeout = await get_master_start_timeout(ops_test)
    await change_master_start_timeout(ops_test, 0)

    # Update the primary unit to have a new RESTART_DELAY. Modifying the Restart delay to 3 minutes
    # should ensure enough time for the test.
    for unit in ops_test.model.applications[app].units:
        if unit.name == primary_name:
            await update_restart_delay(ops_test, unit, RESTART_DELAY)
            break

    # Restart all units "simultaneously".
    await send_signal_to_process(ops_test, primary_name, process, kill_code="SIGTERM")

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
                assert new_primary_name != primary_name

        # Revert the "master_start_timeout" parameter to avoid fail-over again.
        await change_master_start_timeout(ops_test, original_master_start_timeout)

        # Verify new writes are continuing by counting the number of writes before and after a
        # 60 seconds wait (this is a little more than the loop wait configuration, that is
        # considered to trigger a fail-over after master_start_timeout is changed).
        writes = await count_writes(ops_test, primary_name)
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test, primary_name)
                assert more_writes > writes, "writes not continuing to DB"

        for unit in ops_test.model.applications[app].units:
            if unit.name == new_primary_name:
                continue
            await change_wal_settings(ops_test, unit.name, 32, 32, 1)
            break

        # Rotate the WAL segments.
        files = await list_wal_files(ops_test, app)
        host = get_unit_address(ops_test, new_primary_name)
        password = await get_password(ops_test, new_primary_name)
        with db_connect(host, password) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                slot_name = primary_name.replace("/", "_")
                cursor.execute(
                    f"SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = '{slot_name}';"
                )
                cursor.execute("SELECT pg_switch_wal();")
                cursor.execute("CHECKPOINT;")
                cursor.execute("SELECT pg_switch_wal();")
        connection.close()
        new_files = await list_wal_files(ops_test, app)
        for unit_name in files:
            assert not files[unit_name].intersection(
                new_files
            ), "WAL segments weren't correctly rotated"

        # await update_restart_delay(ops_test, primary_unit, ORIGINAL_RESTART_DELAY)

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
