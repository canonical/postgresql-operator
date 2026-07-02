#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest

from .adapters import JujuFixture
from .jubilant_helpers import DATABASE_APP_NAME

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_storage(juju: JujuFixture, charm):
    """Build and deploy the charm and check its storage list."""
    with juju.ext.fast_forward():
        juju.ext.model.deploy(
            charm,
            num_units=1,
            config={"profile": "testing"},
        )
        juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

        logger.info("Checking charm storages")
        expected_storages = ["archive", "data", "logs", "temp"]
        storages = juju.ext.model.list_storage()
        assert len(storages) == 4, f"Expected 4 storages, got: {len(storages)}"
        for index, storage in enumerate(storages):
            assert storage["key"] == f"{expected_storages[index]}/{index}", (
                f"Storage {expected_storages[index]} not found"
            )
