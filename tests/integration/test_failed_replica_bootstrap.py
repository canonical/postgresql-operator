#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    get_primary,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)


async def test_failed_replica_bootstrap(ops_test: OpsTest, charm) -> None:
    """Test failed replica bootstrap."""
    await ops_test.model.deploy(charm, config={"profile": "testing"}, num_units=3)
    async with ops_test.fast_forward():
        logger.info("Waiting for the database to become active")
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

        any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
        primary = await get_primary(ops_test, any_unit)
        replica = next(
            unit.name
            for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            if unit.name != primary
        )

        logger.info(f"Removing the pg_control file from {replica} to make it fail")
        await run_command_on_unit(
            ops_test,
            replica,
            "sudo rm /var/snap/charmed-postgresql/common/var/lib/postgresql/global/pg_control",
        )
        await run_command_on_unit(
            ops_test, replica, "sudo snap restart charmed-postgresql.patroni"
        )

        logger.info("Waiting for the database to become in maintenance")
        application = ops_test.model.applications[DATABASE_APP_NAME]
        await ops_test.model.block_until(
            lambda: "maintenance"
            in {unit.workload_status for unit in application.units if unit.name == replica}
        )

        logger.info("Waiting for the database to become active again")
        await ops_test.model.block_until(
            lambda: "active" in {unit.workload_status for unit in application.units}
        )
