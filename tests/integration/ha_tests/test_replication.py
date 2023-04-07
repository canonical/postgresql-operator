#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import METADATA, app_name
from tests.integration.helpers import CHARM_SERIES, get_primary

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/snap/charmed-postgresql/[0-9]*/usr/bin/patroni"
POSTGRESQL_PROCESS = "/snap/charmed-postgresql/current/usr/lib/postgresql/14/bin/postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        charm = await ops_test.build_charm(".")
        async with ops_test.fast_forward():
            await ops_test.model.deploy(charm, num_units=3, series=CHARM_SERIES)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(
                charm, application_name=APPLICATION_NAME, series=CHARM_SERIES
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


async def test_reelection(ops_test: OpsTest, continuous_writes) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 3:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000)

    unit_name = [unit.name for unit in ops_test.model.applications[app].units][0]
    primary_name = await get_primary(ops_test, unit_name)
    await ops_test.model.applications[app].remove_unit(primary_name)

    unit_name = [unit.name for unit in ops_test.model.applications[app].units][0]
    new_primary_name = await get_primary(ops_test, unit_name)
    assert new_primary_name != primary_name, "primary reelection haven't happened"


async def test_consistency(ops_test: OpsTest, continuous_writes) -> None:
    """Write to primary, read data from secondaries (check consistency)."""


async def test_no_data_replicated_between_clusters(ops_test: OpsTest, continuous_writes) -> None:
    """Check that writes in one cluster not replicated to another cluster."""


async def test_preserve_data_on_delete(ops_test: OpsTest, continuous_writes) -> None:
    """Scale-up, read data from new member, scale down, check that member gone without data."""
