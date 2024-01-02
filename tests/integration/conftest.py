#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import platform

import pytest
from pytest_operator.plugin import OpsTest

arch_mapping = {"x86_64": "amd64", "aarch64": "arm64"}


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest):
    """Build the charm-under-test."""
    # Build charm from local source folder.
    yield await ops_test.build_charm(".")


@pytest.fixture(scope="module")
def cpu_arch():
    return arch_mapping.get(platform.machine(), platform.machine())
