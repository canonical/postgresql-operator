#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from ..helpers import CHARM_BASE
from .helpers import app_name, get_cluster_roles


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm: str) -> None:
    """Build and deploy two PostgreSQL clusters."""
    async with ops_test.fast_forward():
        # Deploy the first cluster with reusable storage
        await ops_test.model.deploy(
            charm,
            num_units=3,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )
        await ops_test.model.wait_for_idle(status="active", timeout=1500)


async def test_default_all(ops_test: OpsTest) -> None:
    app = await app_name(ops_test)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=300)

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[app].units[0].name
            )

            assert len(roles["primaries"]) == 1
            assert len(roles["sync_standbys"]) == 2
            assert len(roles["replicas"]) == 0


async def test_majority(ops_test: OpsTest) -> None:
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous_node_count": "majority"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[app].units[0].name
            )

            assert len(roles["primaries"]) == 1
            assert len(roles["sync_standbys"]) == 1
            assert len(roles["replicas"]) == 1


async def test_constant(ops_test: OpsTest) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous_node_count": "2"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=300)

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[app].units[0].name
            )

            assert len(roles["primaries"]) == 1
            assert len(roles["sync_standbys"]) == 2
            assert len(roles["replicas"]) == 0
