#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import CHARM_BASE, DATABASE_APP_NAME

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_storage(ops_test: OpsTest, charm):
    """Build and deploy the charm and check its storage list."""
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            num_units=1,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

        logger.info("Checking charm storages")
        expected_storages = ["archive", "data", "logs", "temp"]
        storages = await ops_test.model.list_storage()
        assert len(storages) == 4, f"Expected 4 storages, got: {len(storages)}"
        for index, storage in enumerate(storages):
            assert (
                storage["attachments"]["unit-postgresql-0"].__dict__["storage_tag"]
                == f"storage-{expected_storages[index]}-{index}"
            ), f"Storage {expected_storages[index]} not found"
