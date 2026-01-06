#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from asyncio import gather

import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import get_password, run_command_on_unit
from .helpers import (
    APPLICATION_NAME,
    ORIGINAL_RESTART_CONDITION,
    RESTART_CONDITION,
    app_name,
    change_patroni_setting,
    change_wal_settings,
    get_patroni_setting,
    get_postgresql_parameter,
    update_restart_condition,
)


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    yield
    # Clear the written data at the end.
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await ops_test.model
                .applications[APPLICATION_NAME]
                .units[0]
                .run_action("clear-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to clear up continuous_writes table"


@pytest.fixture()
async def loop_wait(ops_test: OpsTest) -> None:
    """Temporary change the loop wait configuration."""
    # Change the parameter that makes Patroni wait for some more time before restarting PostgreSQL.
    initial_loop_wait = await get_patroni_setting(ops_test, "loop_wait")
    yield
    # Rollback to the initial configuration.
    app = await app_name(ops_test)
    patroni_password = await get_password(
        ops_test, ops_test.model.applications[app].units[0].name, "patroni"
    )
    await change_patroni_setting(
        ops_test, "loop_wait", initial_loop_wait, patroni_password, use_random_unit=True
    )


@pytest.fixture(scope="module")
async def primary_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the primary start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    app = await app_name(ops_test)
    patroni_password = await get_password(
        ops_test, ops_test.model.applications[app].units[0].name, "patroni"
    )
    initial_primary_start_timeout = await get_patroni_setting(ops_test, "primary_start_timeout")
    await change_patroni_setting(ops_test, "primary_start_timeout", 0, patroni_password)
    yield
    # Rollback to the initial configuration.
    await change_patroni_setting(
        ops_test,
        "primary_start_timeout",
        initial_primary_start_timeout,
        patroni_password,
        use_random_unit=True,
    )


@pytest.fixture()
async def reset_restart_condition(ops_test: OpsTest):
    """Resets service file delay on all units."""
    app = await app_name(ops_test)

    awaits = []
    for unit in ops_test.model.applications[app].units:
        awaits.append(update_restart_condition(ops_test, unit, RESTART_CONDITION))
    await gather(*awaits)

    yield

    awaits = []
    for unit in ops_test.model.applications[app].units:
        awaits.append(update_restart_condition(ops_test, unit, ORIGINAL_RESTART_CONDITION))
    await gather(*awaits)


@pytest.fixture()
async def wal_settings(ops_test: OpsTest) -> None:
    """Restore the WAL settings to the initial values."""
    # Get the value for each setting.
    initial_max_wal_size = await get_postgresql_parameter(ops_test, "max_wal_size")
    initial_min_wal_size = await get_postgresql_parameter(ops_test, "min_wal_size")
    initial_wal_keep_segments = await get_postgresql_parameter(ops_test, "wal_keep_segments")
    yield
    app = await app_name(ops_test)
    for unit in ops_test.model.applications[app].units:
        # Start Patroni if it was previously stopped.
        await run_command_on_unit(ops_test, unit.name, "snap start charmed-postgresql.patroni")
        patroni_password = await get_password(ops_test, unit.name, "patroni")

        # Rollback to the initial settings.
        await change_wal_settings(
            ops_test,
            unit.name,
            initial_max_wal_size,
            initial_min_wal_size,
            initial_wal_keep_segments,
            patroni_password,
        )
