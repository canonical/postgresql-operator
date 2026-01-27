#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for PostgreSQL stereo mode with watcher.

Tests the deployment and failover scenarios for 2-node PostgreSQL clusters
with a watcher/witness node for quorum.

Test scenarios from acceptance criteria:
1. Replica shutdown: clients rerouted to primary, no significant outage
2. Primary shutdown: replica promoted, old primary becomes replica when healthy
3. Watcher shutdown: no service outage
4. Network isolation variants of above
"""

import logging
from asyncio import gather

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    DATABASE_APP_NAME,
)
from .helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    cut_network_from_unit_without_ip_change,
    get_cluster_roles,
    get_primary,
    restore_network_for_unit_without_ip_change,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

WATCHER_APP_NAME = "postgresql-watcher"


@pytest.fixture(scope="module")
async def watcher_charm(ops_test: OpsTest):
    """Build the watcher charm for testing."""
    charm_path = await ops_test.build_charm("./postgresql-watcher")
    return charm_path


@pytest.mark.abort_on_fail
async def test_build_and_deploy_stereo_mode(ops_test: OpsTest, charm, watcher_charm) -> None:
    """Build and deploy PostgreSQL in stereo mode with watcher.

    Deploys:
    - 2 PostgreSQL units
    - 1 Watcher unit
    - Test application for continuous writes
    """
    async with ops_test.fast_forward():
        await gather(
            # Deploy PostgreSQL with exactly 2 units
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=2,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            # Deploy the watcher charm
            ops_test.model.deploy(
                watcher_charm,
                application_name=WATCHER_APP_NAME,
                num_units=1,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            # Deploy test application
            ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                base=CHARM_BASE,
                channel="edge",
            ),
        )

        # Relate PostgreSQL to test app
        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")

        # Relate PostgreSQL to watcher
        await ops_test.model.relate(f"{DATABASE_APP_NAME}:watcher", f"{WATCHER_APP_NAME}:watcher")

        await ops_test.model.wait_for_idle(status="active", timeout=1800)

    # Verify deployment
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 2
    assert len(ops_test.model.applications[WATCHER_APP_NAME].units) == 1


@pytest.mark.abort_on_fail
async def test_watcher_topology_action(ops_test: OpsTest) -> None:
    """Test the show-topology action on the watcher."""
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]

    action = await watcher_unit.run_action("show-topology")
    action = await action.wait()

    assert action.status == "completed"
    assert "topology" in action.results

    # Verify topology includes PostgreSQL endpoints
    import json

    topology = json.loads(action.results["topology"])
    assert "postgresql_endpoints" in topology
    assert len(topology["postgresql_endpoints"]) == 2


@pytest.mark.abort_on_fail
async def test_replica_shutdown_with_watcher(ops_test: OpsTest, continuous_writes) -> None:
    """Test replica shutdown with watcher providing quorum.

    Expected behavior:
    - All connected clients to the primary should not be interrupted
    - Clients connected to replica should be re-routed to primary
    - No significant outage (less than a minute)
    """
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Get current cluster roles
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)
    primary = original_roles["primaries"][0]

    # Get the replica unit
    replica = None
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        if unit.name != primary:
            replica = unit.name
            break

    assert replica is not None, "Could not find replica unit"
    logger.info(f"Shutting down replica: {replica}")

    # Shutdown the replica
    await ops_test.model.destroy_unit(replica, force=True, destroy_storage=False, max_wait=1500)

    # Verify writes continue (primary should still be available)
    # With watcher, we should maintain quorum
    await are_writes_increasing(ops_test, down_unit=replica)

    # Wait for cluster to stabilize
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Scale back up
    logger.info("Scaling back up after replica shutdown")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    # Verify cluster is healthy
    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert new_roles["primaries"][0] == primary, "Primary should not have changed"

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_primary_shutdown_with_watcher(ops_test: OpsTest, continuous_writes) -> None:
    """Test primary shutdown with watcher providing quorum.

    Expected behavior:
    - Old primary should be network-isolated (Patroni handles this)
    - Replica should be promoted to primary
    - Clients re-routed to new primary
    - When old primary is healthy, it should become a replica
    """
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Get current cluster roles
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)
    original_primary = original_roles["primaries"][0]
    original_replica = original_roles["sync_standbys"][0]

    logger.info(f"Shutting down primary: {original_primary}")

    # Shutdown the primary
    await ops_test.model.destroy_unit(
        original_primary, force=True, destroy_storage=False, max_wait=1500
    )

    # With watcher providing quorum, failover should happen automatically
    # Wait for the replica to be promoted
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Verify writes continue on the new primary
    await are_writes_increasing(ops_test, down_unit=original_primary)

    # Verify the replica was promoted
    remaining_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    new_roles = await get_cluster_roles(ops_test, remaining_unit)
    assert len(new_roles["primaries"]) == 1
    assert new_roles["primaries"][0] == original_replica, (
        f"Replica {original_replica} should have been promoted to primary"
    )

    # Scale back up - the new unit should join as replica
    logger.info("Scaling back up after primary shutdown")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    # Verify cluster structure
    final_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(final_roles["primaries"]) == 1
    assert len(final_roles["sync_standbys"]) == 1

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_watcher_shutdown_no_outage(ops_test: OpsTest, continuous_writes) -> None:
    """Test watcher shutdown - should not cause service outage.

    Expected behavior:
    - No outage experienced by either primary or replica
    - Cluster continues to function (but loses quorum guarantee)
    """
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Get current cluster state
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)

    logger.info("Removing watcher unit")

    # Remove the watcher
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]
    await ops_test.model.destroy_unit(watcher_unit.name, force=True, max_wait=300)

    # Verify writes continue without interruption
    await are_writes_increasing(ops_test)

    # PostgreSQL cluster should remain active
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=300,
        idle_period=30,
    )

    # Verify cluster roles unchanged
    new_roles = await get_cluster_roles(ops_test, any_unit)
    assert new_roles["primaries"] == original_roles["primaries"]

    # Re-deploy watcher
    logger.info("Re-deploying watcher")
    await ops_test.model.applications[WATCHER_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=600)

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_primary_network_isolation_with_watcher(
    ops_test: OpsTest, continuous_writes
) -> None:
    """Test network isolation of primary with watcher.

    Expected behavior:
    - Isolated primary's connections terminated
    - Replica promoted to primary
    - When network restored, old primary becomes replica
    """
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Get current cluster state
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)
    primary = original_roles["primaries"][0]
    replica = original_roles["sync_standbys"][0]

    # Get primary machine name for network manipulation
    primary_unit = None
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        if unit.name == primary:
            primary_unit = unit
            break

    assert primary_unit is not None
    primary_machine = primary_unit.machine.hostname

    logger.info(f"Isolating primary network: {primary} on {primary_machine}")

    try:
        # Cut network from primary
        cut_network_from_unit_without_ip_change(primary_machine)

        # Wait for failover
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            timeout=600,
            idle_period=30,
            raise_on_error=False,  # Primary will be in error state
        )

        # Verify replica was promoted
        new_primary = await get_primary(ops_test, app, down_unit=primary)
        assert new_primary == replica, (
            f"Replica {replica} should have been promoted, but primary is {new_primary}"
        )

    finally:
        # Restore network
        logger.info(f"Restoring network for {primary_machine}")
        restore_network_for_unit_without_ip_change(primary_machine)

    # Wait for cluster to stabilize with restored network
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Verify old primary is now a replica
    final_roles = await get_cluster_roles(ops_test, replica)
    assert primary not in final_roles["primaries"], "Old primary should now be a replica"
    assert replica in final_roles["primaries"], (
        "Replica should remain primary after network restore"
    )

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_replica_network_isolation_with_watcher(
    ops_test: OpsTest, continuous_writes
) -> None:
    """Test network isolation of replica with watcher.

    Expected behavior:
    - Primary continues operating
    - No impact on clients connected to primary
    - Read-only clients re-routed
    """
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Get current cluster state
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)
    primary = original_roles["primaries"][0]
    replica = original_roles["sync_standbys"][0]

    # Get replica machine for network manipulation
    replica_unit = None
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        if unit.name == replica:
            replica_unit = unit
            break

    assert replica_unit is not None
    replica_machine = replica_unit.machine.hostname

    logger.info(f"Isolating replica network: {replica} on {replica_machine}")

    try:
        # Cut network from replica
        cut_network_from_unit_without_ip_change(replica_machine)

        # Verify writes continue on primary
        await are_writes_increasing(ops_test, down_unit=replica)

        # Primary should remain primary
        current_primary = await get_primary(ops_test, app, down_unit=replica)
        assert current_primary == primary, "Primary should not change"

    finally:
        # Restore network
        logger.info(f"Restoring network for {replica_machine}")
        restore_network_for_unit_without_ip_change(replica_machine)

    # Wait for cluster to stabilize
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Verify cluster roles unchanged
    final_roles = await get_cluster_roles(ops_test, any_unit)
    assert final_roles["primaries"][0] == primary

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_watcher_network_isolation(ops_test: OpsTest, continuous_writes) -> None:
    """Test network isolation of watcher.

    Expected behavior:
    - No service outage for PostgreSQL cluster
    - Cluster loses quorum guarantee but continues operating
    """
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Get watcher machine
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]
    watcher_machine = watcher_unit.machine.hostname

    # Get current cluster state
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)

    logger.info(f"Isolating watcher network: {watcher_machine}")

    try:
        # Cut network from watcher
        cut_network_from_unit_without_ip_change(watcher_machine)

        # Verify writes continue without interruption
        await are_writes_increasing(ops_test)

        # Cluster roles should remain unchanged
        current_roles = await get_cluster_roles(ops_test, any_unit)
        assert current_roles["primaries"] == original_roles["primaries"]

    finally:
        # Restore network
        logger.info(f"Restoring watcher network: {watcher_machine}")
        restore_network_for_unit_without_ip_change(watcher_machine)

    # Wait for full recovery
    await ops_test.model.wait_for_idle(status="active", timeout=600)

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_health_check_action(ops_test: OpsTest) -> None:
    """Test the trigger-health-check action on the watcher."""
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]

    action = await watcher_unit.run_action("trigger-health-check")
    action = await action.wait()

    assert action.status == "completed"
    assert "endpoints" in action.results
    assert int(action.results["healthy_count"]) == 2
    assert int(action.results["total_count"]) == 2
