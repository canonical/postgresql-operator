#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest):
    """Build the charm-under-test."""
    # Build charm from local source folder.
    yield await ops_test.build_charm(".")


@pytest.fixture(scope="module")
def juju2(ops_test):
    """Skip test if it is not on juju2."""
    if hasattr(ops_test.model, "list_secrets"):
        pytest.skip("Test can't run on Juju3")


@pytest.fixture(scope="module")
def juju3(ops_test):
    """Skip test if it is not on juju3."""
    if not hasattr(ops_test.model, "list_secrets"):
        pytest.skip("Test can't run on Juju2")
