#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import os
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture(scope="module")
def ops_test(ops_test: OpsTest) -> OpsTest:
    if os.environ.get("CI") == "true":
        # Running in GitHub Actions; skip build step
        # (GitHub Actions uses a separate, cached build step. See .github/workflows/ci.yaml)
        packed_charms = json.loads(os.environ["CI_PACKED_CHARMS"])

        async def build_charm(charm_path, bases_index: int = None) -> Path:
            for charm in packed_charms:
                if Path(charm_path) == Path(charm["directory_path"]):
                    if bases_index is None or bases_index == charm["bases_index"]:
                        return charm["file_path"]
            raise ValueError(f"Unable to find .charm file for {bases_index=} at {charm_path=}")

        ops_test.build_charm = build_charm
    return ops_test


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest):
    """Build the charm-under-test."""
    # Build charm from local source folder.
    yield await ops_test.build_charm(".")
