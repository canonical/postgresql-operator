#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
from typing import Optional

import pytest
from pytest_operator.plugin import OpsTest


def pytest_addoption(parser):
    parser.addoption(
        "--postgresql_charm",
        action="store",
        default=None,
        help="The location of prebuilt mongodb-k8s charm",
    )


@pytest.fixture(scope="session")
def cmd_postgresql_charm_charm(request) -> Optional[Path]:
    """Fixture to optionally pass a prebuilt charm to deploy."""
    charm_path = request.config.getoption("--postgresql_charm")
    if charm_path:
        path = Path(charm_path).absolute()
        if path.exists():
            return path


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest, cmd_postgresql_charm_charm) -> Path:
    """Build the charm-under-test."""
    if cmd_postgresql_charm_charm:
        yield cmd_postgresql_charm_charm
    else:
        # Build charm from local source folder.
        yield await ops_test.build_charm(".")
