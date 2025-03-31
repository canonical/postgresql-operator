#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest

from constants import POSTGRESQL_SNAP_NAME, SNAP_PACKAGES

from . import architecture
from .helpers import (
    DATABASE_APP_NAME,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)


async def test_snap_revision_mismatch(ops_test: OpsTest, charm) -> None:
    """Test snap revision mismatch."""
    await ops_test.model.deploy(charm, config={"profile": "testing"})
    async with ops_test.fast_forward():
        logger.info("Waiting for the database to become active")
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

        unit_name = f"{DATABASE_APP_NAME}/0"
        old_snap_revision = "142" if architecture.architecture == "arm64" else "143"
        expected_snap_revision = next(
            iter([
                snap[1]["revision"][
                    architecture.architecture.replace("arm64", "aarch64").replace(
                        "amd64", "x86_64"
                    )
                ]
                for snap in SNAP_PACKAGES
                if snap[0] == POSTGRESQL_SNAP_NAME
            ])
        )

        logger.info(
            f"Downgrading {unit_name} snap revision from {expected_snap_revision} to {old_snap_revision}"
        )
        await run_command_on_unit(
            ops_test,
            unit_name,
            f"sudo snap refresh charmed-postgresql --revision {old_snap_revision}",
        )
        await run_command_on_unit(
            ops_test, unit_name, "sudo snap start charmed-postgresql.patroni"
        )

        logger.info("Waiting for the database to become blocked")
        application = ops_test.model.applications[DATABASE_APP_NAME]
        await ops_test.model.block_until(
            lambda: "blocked" in {unit.workload_status for unit in application.units}
        )
        assert application.units[0].workload_status_message == "Snap revision mismatch"

        logger.info(
            f"Upgrading the snap revision back to the expected one ({expected_snap_revision})"
        )
        await run_command_on_unit(
            ops_test,
            unit_name,
            f"sudo snap refresh charmed-postgresql --revision {expected_snap_revision}",
        )
        await run_command_on_unit(
            ops_test, unit_name, "sudo snap start charmed-postgresql.patroni"
        )

        logger.info("Waiting for the database to become active again")
        await ops_test.model.block_until(
            lambda: "active" in {unit.workload_status for unit in application.units}
        )
