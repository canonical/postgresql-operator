#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    app_name,
    check_writes,
    count_writes,
    get_password,
    is_cluster_updated,
    start_continuous_writes,
)
from tests.integration.helpers import (
    CHARM_SERIES,
    db_connect,
    get_primary,
    scale_application,
)


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


async def test_reelection(ops_test: OpsTest, continuous_writes, primary_start_timeout) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await scale_application(ops_test, app, 2)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Remove the primary unit.
    primary_name = await get_primary(ops_test, app)
    await ops_test.model.applications[app].remove_unit(primary_name)

    # Wait and get the primary again (which can be any unit, including the previous primary).
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    # Check whether writes are increasing.
    writes = await count_writes(ops_test, primary_name)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            more_writes = await count_writes(ops_test, primary_name)
            assert more_writes > writes, "writes not continuing to DB"

    # Verify that a new primary gets elected (ie old primary is secondary).
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
            assert new_primary_name != primary_name, "primary reelection hasn't happened"

    # Verify that all the units are up-to-date.
    await is_cluster_updated(ops_test, primary_name)


async def test_consistency(ops_test: OpsTest, continuous_writes) -> None:
    """Write to primary, read data from secondaries (check consistency)."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    writes = await count_writes(ops_test, primary_name)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            more_writes = await count_writes(ops_test, primary_name)
            assert more_writes > writes, "writes not continuing to DB"

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    await check_writes(ops_test)


async def test_no_data_replicated_between_clusters(ops_test: OpsTest, continuous_writes) -> None:
    """Check that writes in one cluster are not replicated to another cluster."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Deploy another cluster.
    new_cluster_app = f"second-{app}"
    if not await app_name(ops_test):
        charm = await ops_test.build_charm(".")
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                charm, application_name=new_cluster_app, num_units=2, series=CHARM_SERIES
            )
            await ops_test.model.wait_for_idle(apps=[app], status="active")

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    writes = await count_writes(ops_test, primary_name)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            more_writes = await count_writes(ops_test, primary_name)
            assert more_writes > writes, "writes not continuing to DB"

    # Verify that no writes to the first cluster were missed after stopping the writes.
    await check_writes(ops_test)

    # Verify that the data from the first cluster wasn't replicated to the second cluster.
    password = await get_password(ops_test, app=new_cluster_app)
    for unit in ops_test.model.applications[new_cluster_app].units:
        try:
            with db_connect(
                host=unit.public_address, password=password
            ) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_name = 'continuous_writes');"
                )
                assert not cursor.fetchone()[
                    0
                ], "table 'continuous_writes' was replicated to the second cluster"
        finally:
            connection.close()


async def test_preserve_data_on_delete(ops_test: OpsTest, continuous_writes) -> None:
    """Scale-up, read data from new member, scale down, check that member gone without data."""
