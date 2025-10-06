#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import markers
from ..helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    db_connect,
    get_machine_from_unit,
    get_password,
    get_unit_address,
    run_command_on_unit,
)
from .conftest import APPLICATION_NAME
from .helpers import (
    METADATA,
    ORIGINAL_RESTART_CONDITION,
    add_unit_with_storage,
    app_name,
    are_all_db_processes_down,
    are_writes_increasing,
    change_patroni_setting,
    change_wal_settings,
    check_writes,
    cut_network_from_unit,
    cut_network_from_unit_without_ip_change,
    fetch_cluster_members,
    get_controller_machine,
    get_patroni_setting,
    get_primary,
    get_unit_ip,
    is_cluster_updated,
    is_connection_possible,
    is_machine_reachable_from,
    is_postgresql_ready,
    is_replica,
    is_secondary_up_to_date,
    list_wal_files,
    restore_network_for_unit,
    restore_network_for_unit_without_ip_change,
    reused_replica_storage,
    send_signal_to_process,
    start_continuous_writes,
    storage_id,
    storage_type,
    update_restart_condition,
    wait_network_restore,
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
                storage={"pgdata": {"pool": "lxd-btrfs", "size": 2048}},
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
async def test_storage_re_use(ops_test, continuous_writes):
    """Verifies that database units with attached storage correctly repurpose storage.

    It is not enough to verify that Juju attaches the storage. Hence test checks that the
    postgresql properly uses the storage that was provided. (ie. doesn't just re-sync everything
    from primary, but instead computes a diff between current storage and primary storage.)
    """
    app = await app_name(ops_test)
    if storage_type(ops_test, app) == "rootfs":
        pytest.skip(
            "reuse of storage can only be used on deployments with persistent storage not on rootfs deployments"
        )

    # removing the only replica can be disastrous
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1500)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # remove a unit and attach it's storage to a new unit
    for unit in ops_test.model.applications[app].units:
        if await is_replica(ops_test, unit.name):
            break
    unit_storage_id = storage_id(ops_test, unit.name)
    expected_units = len(ops_test.model.applications[app].units) - 1
    await ops_test.model.destroy_unit(unit.name)
    await ops_test.model.wait_for_idle(
        apps=[app], status="active", timeout=1000, wait_for_exact_units=expected_units
    )
    new_unit = await add_unit_with_storage(ops_test, app, unit_storage_id)

    assert await reused_replica_storage(ops_test, new_unit.name), (
        "attached storage not properly reused by Postgresql."
    )

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await check_writes(ops_test)

    # Verify that new instance is up-to-date.
    assert await is_secondary_up_to_date(ops_test, new_unit.name, total_expected_writes), (
        "new instance not up to date."
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("process", DB_PROCESSES)
@pytest.mark.parametrize("signal", ["SIGTERM", "SIGKILL"])
async def test_interruption_db_process(
    ops_test: OpsTest, process: str, signal: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Interrupt the database process.
    await send_signal_to_process(ops_test, primary_name, process, signal)

    # Wait some time to elect a new primary.
    sleep(MEDIAN_ELECTION_TIME * 6)

    async with ops_test.fast_forward():
        await are_writes_increasing(ops_test, primary_name)

        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app)
                assert new_primary_name != primary_name

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    await is_cluster_updated(ops_test, primary_name)


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
    patroni_password = await get_password(
        ops_test, ops_test.model.applications[app].units[0].name, "patroni"
    )

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


@pytest.mark.abort_on_fail
@pytest.mark.skip(reason="Unstable")
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
            assert not files[unit_name].intersection(new_files), (
                "WAL segments weren't correctly rotated"
            )

        # Start the systemd service in the old primary.
        await run_command_on_unit(ops_test, primary_name, "snap start charmed-postgresql.patroni")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await is_postgresql_ready(ops_test, primary_name)

    await is_cluster_updated(ops_test, primary_name)


@pytest.mark.abort_on_fail
@markers.amd64_only
async def test_network_cut(ops_test: OpsTest, continuous_writes, primary_start_timeout):
    """Completely cut and restore network."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Get unit hostname and IP.
    primary_hostname = await get_machine_from_unit(ops_test, primary_name)
    primary_ip = await get_unit_ip(ops_test, primary_name)

    # Verify that connection is possible.
    logger.info("checking whether the connectivity to the database is working")
    assert await is_connection_possible(ops_test, primary_name), (
        f"Connection {primary_name} is not possible"
    )

    logger.info(f"Cutting network for {primary_name}")
    cut_network_from_unit(primary_hostname)

    # Verify machine is not reachable from peer units.
    all_units_names = [unit.name for unit in ops_test.model.applications[app].units]
    for unit_name in set(all_units_names) - {primary_name}:
        logger.info(f"checking for no connectivity between {primary_name} and {unit_name}")
        hostname = await get_machine_from_unit(ops_test, unit_name)
        assert not is_machine_reachable_from(hostname, primary_hostname), (
            "unit is reachable from peer"
        )

    # Verify machine is not reachable from controller.
    logger.info(f"checking for no connectivity between {primary_name} and the controller")
    controller = await get_controller_machine(ops_test)
    assert not is_machine_reachable_from(controller, primary_hostname), (
        "unit is reachable from controller"
    )

    # Verify that connection is not possible.
    logger.info("checking whether the connectivity to the database is not working")
    assert not await is_connection_possible(ops_test, primary_name), (
        "Connection is possible after network cut"
    )

    async with ops_test.fast_forward():
        logger.info("checking whether writes are increasing")
        await are_writes_increasing(ops_test, primary_name)

        logger.info("checking whether a new primary was elected")
        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
                assert new_primary_name != primary_name

    logger.info(f"Restoring network for {primary_name}")
    restore_network_for_unit(primary_hostname)

    # Wait until the cluster becomes idle (some operations like updating the member
    # IP are made).
    logger.info("waiting for cluster to become idle after updating member IP")
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[app],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
            idle_period=30,
        )

    # Wait the LXD unit has its IP updated.
    logger.info("waiting for IP address to be updated on Juju unit")
    await wait_network_restore(ops_test, primary_name, primary_ip)

    # Verify that the database service got restarted and is ready in the old primary.
    logger.info(f"waiting for the database service to be ready on {primary_name}")
    assert await is_postgresql_ready(ops_test, primary_name, use_ip_from_inside=True)

    # Verify that connection is possible.
    logger.info("checking whether the connectivity to the database is working")
    assert await is_connection_possible(ops_test, primary_name, use_ip_from_inside=True), (
        "Connection is not possible after network restore"
    )

    await is_cluster_updated(ops_test, primary_name, use_ip_from_inside=True)


@pytest.mark.abort_on_fail
@markers.amd64_only
async def test_network_cut_without_ip_change(
    ops_test: OpsTest, continuous_writes, primary_start_timeout
):
    """Completely cut and restore network (situation when the unit IP doesn't change)."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Get unit hostname and IP.
    primary_hostname = await get_machine_from_unit(ops_test, primary_name)

    # Verify that connection is possible.
    logger.info("checking whether the connectivity to the database is working")
    assert await is_connection_possible(ops_test, primary_name), (
        f"Connection {primary_name} is not possible"
    )

    logger.info(f"Cutting network for {primary_name}")
    cut_network_from_unit_without_ip_change(primary_hostname)

    # Verify machine is not reachable from peer units.
    all_units_names = [unit.name for unit in ops_test.model.applications[app].units]
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            for unit_name in set(all_units_names) - {primary_name}:
                logger.info(f"checking for no connectivity between {primary_name} and {unit_name}")
                hostname = await get_machine_from_unit(ops_test, unit_name)
                assert not is_machine_reachable_from(hostname, primary_hostname), (
                    "unit is reachable from peer"
                )

    # Verify machine is not reachable from controller.
    logger.info(f"checking for no connectivity between {primary_name} and the controller")
    controller = await get_controller_machine(ops_test)
    assert not is_machine_reachable_from(controller, primary_hostname), (
        "unit is reachable from controller"
    )

    # Verify that connection is not possible.
    logger.info("checking whether the connectivity to the database is not working")
    assert not await is_connection_possible(ops_test, primary_name), (
        "Connection is possible after network cut"
    )

    async with ops_test.fast_forward():
        logger.info("checking whether writes are increasing")
        await are_writes_increasing(ops_test, primary_name, use_ip_from_inside=True)

        logger.info("checking whether a new primary was elected")
        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
                assert new_primary_name != primary_name

    logger.info(f"Restoring network for {primary_name}")
    restore_network_for_unit_without_ip_change(primary_hostname)

    # Wait until the cluster becomes idle.
    logger.info("waiting for cluster to become idle")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    # Verify that the database service got restarted and is ready in the old primary.
    logger.info(f"waiting for the database service to be ready on {primary_name}")
    assert await is_postgresql_ready(ops_test, primary_name)

    # Verify that connection is possible.
    logger.info("checking whether the connectivity to the database is working")
    assert await is_connection_possible(ops_test, primary_name), (
        "Connection is not possible after network restore"
    )

    await is_cluster_updated(ops_test, primary_name, use_ip_from_inside=True)
