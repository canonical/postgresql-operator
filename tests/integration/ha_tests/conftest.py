#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.helpers import (
    ORIGINAL_RESTART_DELAY,
    app_name,
    change_master_start_timeout,
    change_wal_settings,
    get_master_start_timeout,
    get_postgresql_parameter,
    update_restart_delay,
)
from tests.integration.helpers import run_command_on_unit

APPLICATION_NAME = "application"


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    yield
    # Clear the written data at the end.
    action = (
        await ops_test.model.applications[APPLICATION_NAME]
        .units[0]
        .run_action("clear-continuous-writes")
    )
    await action.wait()


@pytest.fixture()
async def master_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the master start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    initial_master_start_timeout = await get_master_start_timeout(ops_test)
    yield
    # Rollback to the initial configuration.
    await change_master_start_timeout(ops_test, initial_master_start_timeout, use_random_unit=True)


@pytest.fixture()
async def reset_restart_delay(ops_test: OpsTest):
    """Resets service file delay on all units."""
    yield
    app = await app_name(ops_test)
    for unit in ops_test.model.applications[app].units:
        await update_restart_delay(ops_test, unit, ORIGINAL_RESTART_DELAY)


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
        await run_command_on_unit(ops_test, unit.name, "systemctl start patroni")

        # Rollback to the initial settings.
        await change_wal_settings(
            ops_test,
            unit.name,
            initial_max_wal_size,
            initial_min_wal_size,
            initial_wal_keep_segments,
        )
