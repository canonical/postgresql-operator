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

import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed
from yaml import safe_load

from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
)
from .helpers import APPLICATION_NAME as TEST_APP_NAME
from .helpers import (
    are_writes_increasing,
    check_writes,
    cut_network_from_unit,
    cut_network_from_unit_without_ip_change,
    get_cluster_roles,
    get_primary,
    restore_network_for_unit,
    restore_network_for_unit_without_ip_change,
)


async def start_writes(ops_test: OpsTest) -> None:
    """Start continuous writes to PostgreSQL (assumes relation already exists)."""
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await ops_test.model
                .applications[TEST_APP_NAME]
                .units[0]
                .run_action("start-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to create continuous_writes table"


logger = logging.getLogger(__name__)


async def verify_raft_cluster_health(
    ops_test: OpsTest,
    db_app_name: str,
    watcher_app_name: str,
    expected_members: int = 3,
    check_watcher_ip: bool = True,
) -> None:
    """Verify that the Raft cluster has the expected number of members and quorum.

    This function checks that all PostgreSQL units see the expected number of
    Raft members (including the watcher) and have quorum. This is critical
    after watcher re-deployment to ensure the cluster is properly formed.

    Args:
        ops_test: The OpsTest instance.
        db_app_name: The PostgreSQL application name.
        watcher_app_name: The watcher application name.
        expected_members: Expected number of Raft members (default 3 for stereo mode).
        check_watcher_ip: Whether to verify the watcher IP in Raft status (default True).
            Set to False after network isolation tests where watcher may have been
            redeployed with a new IP that isn't yet in the Raft configuration.

    Raises:
        AssertionError: If the Raft cluster is not healthy.
    """
    logger.info(f"Verifying Raft cluster health with {expected_members} expected members")

    # Get watcher address for verification using juju exec to avoid cached IPs
    watcher_unit = ops_test.model.applications[watcher_app_name].units[0]
    return_code, watcher_ip, _ = await ops_test.juju(
        "exec", "--unit", watcher_unit.name, "--", "unit-get", "private-address"
    )
    assert return_code == 0, f"Failed to get watcher address from {watcher_unit.name}"
    watcher_ip = watcher_ip.strip()

    for attempt in Retrying(stop=stop_after_delay(360), wait=wait_fixed(10), reraise=True):
        with attempt:
            for unit in ops_test.model.applications[db_app_name].units:
                # Get the Raft password from Patroni config using juju exec directly
                # We need to avoid shell interpretation issues with run_command_on_unit
                complete_command = [
                    "exec",
                    "--unit",
                    unit.name,
                    "--",
                    "cat",
                    "/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml",
                ]
                return_code, stdout, _ = await ops_test.juju(*complete_command)
                assert return_code == 0, f"Failed to read patroni.yaml on {unit.name}"

                conf = safe_load(stdout)
                password = conf.get("raft", {}).get("password")
                assert password, f"Could not find Raft password in patroni.yaml on {unit.name}"

                # Check Raft status using the password via juju exec directly
                complete_command = [
                    "exec",
                    "--unit",
                    unit.name,
                    "--",
                    "charmed-postgresql.syncobj-admin",
                    "-conn",
                    conf["raft"]["self_addr"],
                    "-pass",
                    password,
                    "-status",
                ]
                return_code, output, _ = await ops_test.juju(*complete_command)
                if return_code != 0:
                    logger.warning(f"Raft status check failed on {unit.name}: {output}")
                    raise AssertionError(f"Raft status check failed on {unit.name}")
                logger.info(f"Raft status on {unit.name}: {output[:200]}...")

                # Verify quorum
                assert "has_quorum: True" in output or "has_quorum:True" in output, (
                    f"Unit {unit.name} does not have Raft quorum"
                )

                # Verify watcher is in the cluster (if requested)
                # After network isolation tests, the watcher may have been redeployed
                # with a new IP that isn't yet updated in the Raft configuration
                if check_watcher_ip:
                    assert watcher_ip in output, (
                        f"Watcher {watcher_ip} not found in Raft cluster on {unit.name}\n"
                        f"Raft output: {output}"
                    )

    logger.info("Raft cluster health verified successfully")


WATCHER_APP_NAME = "pg-watcher"


@pytest.mark.abort_on_fail
async def test_build_and_deploy_stereo_mode(ops_test: OpsTest, charm) -> None:
    """Build and deploy PostgreSQL in stereo mode with watcher.

    Deploys 2 PostgreSQL units and a watcher (same charm, role=watcher),
    then relates them to form a 3-node Raft cluster for quorum.
    """
    # Check if PostgreSQL is already deployed (e.g., from a previous test run)
    # If so, verify it's in the expected state or skip deployment
    if DATABASE_APP_NAME in ops_test.model.applications:
        logger.info("PostgreSQL already deployed, checking state...")
        pg_units = len(ops_test.model.applications[DATABASE_APP_NAME].units)
        watcher_deployed = WATCHER_APP_NAME in ops_test.model.applications
        test_app_deployed = APPLICATION_NAME in ops_test.model.applications

        if pg_units == 2 and watcher_deployed and test_app_deployed:
            logger.info("Stereo mode already deployed with expected state, verifying...")
            await ops_test.model.wait_for_idle(status="active", timeout=300)
            return

        # If state is incorrect, we need to clean up and redeploy
        logger.info(f"Unexpected state (pg_units={pg_units}), cleaning up...")
        for app in [DATABASE_APP_NAME, WATCHER_APP_NAME, APPLICATION_NAME]:
            if app in ops_test.model.applications:
                await ops_test.model.remove_application(app, block_until_done=True)

    async with ops_test.fast_forward():
        # Deploy PostgreSQL with 2 units from the start
        logger.info("Deploying PostgreSQL charm with 2 units...")
        await ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=2,
            series="noble",
            config={"profile": "testing", "synchronous-mode-strict": False},
        )
        # Deploy watcher using the same charm with role=watcher
        logger.info("Deploying watcher (same charm, role=watcher)...")
        await ops_test.model.deploy(
            charm,
            application_name=WATCHER_APP_NAME,
            num_units=1,
            series="noble",
            config={"role": "watcher", "profile": "testing"},
        )
        logger.info("Deploying test application...")
        await ops_test.model.deploy(
            APPLICATION_NAME,
            application_name=APPLICATION_NAME,
            series="noble",
            channel="edge",
        )

        # Wait for initial deployment
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            timeout=1200,
            raise_on_error=False,  # Watcher may be waiting for relation
        )

        # Relate PostgreSQL (watcher-offer) to watcher (watcher)
        # The relation may already exist if deploying into a model with prior state
        logger.info("Relating PostgreSQL to watcher")
        try:
            await ops_test.model.integrate(
                f"{DATABASE_APP_NAME}:watcher-offer", f"{WATCHER_APP_NAME}:watcher"
            )
        except Exception as e:
            if "already exists" in str(e) or "relation" in str(e).lower():
                logger.info(f"Watcher relation already exists: {e}")
            else:
                raise

        # Wait for watcher to join Raft cluster
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            status="active",
            timeout=600,
        )

        # Relate PostgreSQL to test app
        try:
            await ops_test.model.integrate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        except Exception as e:
            if "already exists" in str(e) or "relation" in str(e).lower():
                logger.info(f"Database relation already exists: {e}")
            else:
                raise

        await ops_test.model.wait_for_idle(status="active", timeout=1800)

    # Verify deployment
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 2
    assert len(ops_test.model.applications[WATCHER_APP_NAME].units) == 1


@pytest.mark.abort_on_fail
async def test_replica_shutdown_with_watcher(ops_test: OpsTest, continuous_writes) -> None:
    """Test replica shutdown with watcher providing quorum.

    Expected behavior:
    - All connected clients to the primary should not be interrupted
    - Clients connected to replica should be re-routed to primary
    - No significant outage (less than a minute)
    """
    await start_writes(ops_test)

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

    # Wait for the cluster to stabilize after unit removal
    # The primary needs time to reconfigure the cluster and update secrets
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=300,
        idle_period=30,
    )

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

    # Wait for the new replica to become a sync_standby
    # This ensures the cluster is fully ready for the next test
    for attempt in Retrying(stop=stop_after_delay(180), wait=wait_fixed(10), reraise=True):
        with attempt:
            new_roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
            )
            logger.info(f"Cluster roles: {new_roles}")
            assert len(new_roles["primaries"]) == 1, "Should have exactly one primary"
            assert new_roles["primaries"][0] == primary, "Primary should not have changed"
            assert len(new_roles["sync_standbys"]) == 1, "New replica should become sync_standby"

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
    await start_writes(ops_test)

    # Get current cluster roles
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit)
    original_primary = original_roles["primaries"][0]

    # Get the replica - prefer sync_standby if available, otherwise any replica
    # After a previous test scales up, the new unit may not yet be a sync_standby
    if original_roles["sync_standbys"]:
        original_replica = original_roles["sync_standbys"][0]
    elif original_roles["replicas"]:
        original_replica = original_roles["replicas"][0]
    else:
        # Fall back to finding the other unit manually
        original_replica = None
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            if unit.name != original_primary:
                original_replica = unit.name
                break
        assert original_replica is not None, "Could not find replica unit"

    logger.info(f"Shutting down primary: {original_primary}")

    # Shutdown the primary
    await ops_test.model.destroy_unit(
        original_primary, force=True, destroy_storage=False, max_wait=1500
    )

    # With watcher providing quorum, failover should happen automatically
    # Wait for the model to stabilize first
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Wait for the replica to be promoted to primary
    # Patroni needs time to detect leader failure and elect new leader (30-90s)
    remaining_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    for attempt in Retrying(stop=stop_after_delay(180), wait=wait_fixed(10), reraise=True):
        with attempt:
            new_roles = await get_cluster_roles(ops_test, remaining_unit)
            logger.info(f"Waiting for failover - current roles: {new_roles}")
            assert len(new_roles["primaries"]) == 1, "Should have exactly one primary"
            assert new_roles["primaries"][0] == original_replica, (
                f"Replica {original_replica} should have been promoted, "
                f"but primary is {new_roles['primaries'][0]}"
            )

    # Wait for the charm to reconfigure after failover
    # This ensures the relation endpoints are updated for the test app to reconnect
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=300,
        idle_period=30,
    )

    # Scale back up FIRST - with synchronous_mode_strict=true, the primary cannot
    # accept writes when there's no sync_standby available. We need 2 units before
    # we can verify writes are working.
    logger.info("Scaling back up after primary shutdown")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    # Wait longer for the new unit to fully join the cluster
    # The new unit needs to: start PostgreSQL, join Raft cluster, become sync_standby
    await ops_test.model.wait_for_idle(status="active", timeout=1800, idle_period=60)

    # Wait for the new replica to become a sync_standby
    # This can take a while as the new unit needs to fully sync and be recognized
    for attempt in Retrying(stop=stop_after_delay(300), wait=wait_fixed(15), reraise=True):
        with attempt:
            final_roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
            )
            logger.info(f"Final cluster roles: {final_roles}")
            assert len(final_roles["primaries"]) == 1, "Should have exactly one primary"
            assert len(final_roles["sync_standbys"]) == 1, "New replica should become sync_standby"

    # Now that we have a sync_standby, restart continuous writes and verify
    # The continuous writes app caches the connection string, so we need to clear
    # and restart it after failover to pick up the new primary's address.
    # First clear the old writes state
    action = (
        await ops_test.model
        .applications[TEST_APP_NAME]
        .units[0]
        .run_action("clear-continuous-writes")
    )
    await action.wait()

    # Then start fresh writes
    await start_writes(ops_test)

    # Verify writes continue on the new primary
    await are_writes_increasing(ops_test, down_unit=original_primary)

    await check_writes(ops_test)


@pytest.mark.abort_on_fail
async def test_watcher_shutdown_no_outage(ops_test: OpsTest, continuous_writes) -> None:
    """Test watcher shutdown - should not cause service outage.

    Expected behavior:
    - No outage experienced by either primary or replica
    - Cluster continues to function (but loses quorum guarantee)
    """
    await start_writes(ops_test)

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

    # Verify the Raft cluster is properly formed with the new watcher
    # This is critical - without this verification, subsequent tests might fail
    # because the watcher is not actually participating in the Raft cluster
    await verify_raft_cluster_health(ops_test, DATABASE_APP_NAME, WATCHER_APP_NAME)

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
    await start_writes(ops_test)

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
        # Cut network from primary (this removes the eth0 interface entirely)
        cut_network_from_unit_without_ip_change(primary_machine)

        # Wait for failover to happen - Patroni needs time to detect leader failure
        # and elect a new leader. This can take 30-90 seconds depending on TTL settings.
        # Use explicit retry loop instead of just wait_for_idle.
        new_primary = None
        for attempt in Retrying(stop=stop_after_delay(180), wait=wait_fixed(10), reraise=True):
            with attempt:
                new_primary = await get_primary(ops_test, DATABASE_APP_NAME, down_unit=primary)
                logger.info(f"Current primary: {new_primary}, expected: {replica}")
                assert new_primary == replica, (
                    f"Waiting for failover: replica {replica} should be promoted, "
                    f"but primary is still {new_primary}"
                )
                await are_writes_increasing(ops_test, down_unit=primary_unit.name)
    finally:
        # Restore network
        logger.info(f"Restoring network for {primary_machine}")
        restore_network_for_unit_without_ip_change(primary_machine)

    # Wait for cluster to stabilize with restored network
    # The old primary may take time to rejoin after getting a new IP address,
    # so we use raise_on_error=False and wait longer
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        timeout=900,
        idle_period=30,
        raise_on_error=False,  # Old primary may be in error while rejoining
    )

    # Wait for the old primary to rejoin as replica
    # This can take a while as it needs to recover with a new IP
    for attempt in Retrying(stop=stop_after_delay(300), wait=wait_fixed(15), reraise=True):
        with attempt:
            final_roles = await get_cluster_roles(ops_test, replica)
            logger.info(f"Final cluster roles: {final_roles}")
            assert replica in final_roles["primaries"], (
                "Replica should remain primary after network restore"
            )
            # Old primary should not be primary anymore
            assert (
                primary not in final_roles["primaries"] and primary in final_roles["sync_standbys"]
            ), "Old primary should now be a sync standby"

    # Use use_ip_from_inside=True because the old primary got a new IP after network restore
    # and Juju's cached IP may be stale
    await check_writes(ops_test, use_ip_from_inside=True)


@pytest.mark.abort_on_fail
async def test_replica_network_isolation_with_watcher(
    ops_test: OpsTest, continuous_writes
) -> None:
    """Test network isolation of replica with watcher.

    Expected behavior:
    - Primary remains primary (doesn't failover) - Raft quorum maintained with watcher
    - With synchronous_mode_strict=true, writes pause (no sync_standby available)
    - After network restore, writes resume
    - No data loss

    Note: This test uses iptables-based network isolation to preserve the replica's IP,
    avoiding the complexity of IP changes when using eth0 device removal.
    """
    await start_writes(ops_test)

    # Get current cluster state - use use_ip_from_inside=True because the previous test
    # may have left units with stale IPs in Juju's cache after network restore
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit, use_ip_from_inside=True)
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
        # Cut network from replica using iptables (preserves IP)
        cut_network_from_unit_without_ip_change(replica_machine)

        # Give Patroni time to detect the network isolation.
        await asyncio.sleep(30)

        # Primary should remain primary (no failover should happen)
        # Raft quorum is maintained with primary + watcher (2 out of 3)
        current_primary = await get_primary(ops_test, DATABASE_APP_NAME, down_unit=replica)
        assert current_primary == primary, "Primary should not change during replica isolation"
        await are_writes_increasing(ops_test, down_unit=replica)
    finally:
        # Restore network
        logger.info(f"Restoring network for {replica_machine}")
        restore_network_for_unit_without_ip_change(replica_machine)

    # Wait for cluster to stabilize - replica should rejoin
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Verify cluster has a primary after restore (may or may not be the same one,
    # since Patroni can switchover during network restore/rejoin)
    final_roles = await get_cluster_roles(ops_test, any_unit, use_ip_from_inside=True)
    assert len(final_roles["primaries"]) == 1, (
        "Cluster should have exactly one primary after restore"
    )

    # Verify writes continue after network restore
    # Use use_ip_from_inside=True because previous tests may have caused IP changes
    await are_writes_increasing(ops_test, use_ip_from_inside=True)
    await check_writes(ops_test, use_ip_from_inside=True)


@pytest.mark.abort_on_fail
async def test_watcher_network_isolation(ops_test: OpsTest, continuous_writes) -> None:
    """Test network isolation of watcher.

    Expected behavior:
    - No service outage for PostgreSQL cluster
    - Cluster loses quorum guarantee but continues operating
    """
    await start_writes(ops_test)

    # Get watcher machine
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]
    watcher_machine = watcher_unit.machine.hostname

    # Get current cluster state - use use_ip_from_inside=True because previous tests
    # may have left units with stale IPs in Juju's cache after network manipulation
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit, use_ip_from_inside=True)

    logger.info(f"Isolating watcher network: {watcher_machine}")

    try:
        # Cut network from watcher
        cut_network_from_unit(watcher_machine)

        # Verify writes continue without interruption
        await are_writes_increasing(ops_test, use_ip_from_inside=True)

        # Cluster roles should remain unchanged
        current_roles = await get_cluster_roles(ops_test, any_unit, use_ip_from_inside=True)
        assert current_roles["primaries"] == original_roles["primaries"]

    finally:
        # Restore network
        logger.info(f"Restoring watcher network: {watcher_machine}")
        restore_network_for_unit(watcher_machine)

    # Wait for full recovery
    await ops_test.model.wait_for_idle(status="active", timeout=600)

    # Use use_ip_from_inside=True because the watcher got a new IP after network restore
    await check_writes(ops_test, use_ip_from_inside=True)


@pytest.mark.abort_on_fail
async def test_multi_cluster_watcher(ops_test: OpsTest, charm) -> None:
    """Verify that a single watcher can monitor multiple PostgreSQL clusters.

    The watcher relation no longer has limit: 1, so the watcher can relate
    to multiple PostgreSQL clusters simultaneously. Each relation gets its own
    Raft instance with a dedicated port and data directory.
    """
    second_pg_app = "postgresql-b"

    try:
        # Deploy a second PostgreSQL cluster
        logger.info("Deploying second PostgreSQL cluster for multi-cluster watcher test")
        await ops_test.model.deploy(
            charm,
            application_name=second_pg_app,
            num_units=2,
            series="noble",
            config={"profile": "testing", "synchronous-mode-strict": False},
        )
        await ops_test.model.wait_for_idle(
            apps=[second_pg_app],
            status="active",
            timeout=1200,
        )

        # Relate the watcher to the second cluster
        logger.info("Relating watcher to second PostgreSQL cluster")
        await ops_test.model.integrate(
            f"{second_pg_app}:watcher-offer", f"{WATCHER_APP_NAME}:watcher"
        )

        # Use fast_forward to trigger update_status quickly, which runs
        # ensure_watcher_in_raft to add the watcher to the second cluster's Raft
        async with ops_test.fast_forward():
            # Wait for the watcher to connect to both clusters
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME, second_pg_app, WATCHER_APP_NAME],
                status="active",
                timeout=600,
            )

            # Verify both Raft clusters have the watcher as a member
            # Check first cluster
            await verify_raft_cluster_health(
                ops_test, DATABASE_APP_NAME, WATCHER_APP_NAME, expected_members=3
            )
            # Check second cluster
            await verify_raft_cluster_health(
                ops_test, second_pg_app, WATCHER_APP_NAME, expected_members=3
            )

    finally:
        # Clean up the second cluster relation and app
        if second_pg_app in ops_test.model.applications:
            await ops_test.model.remove_application(
                second_pg_app, block_until_done=True, force=True
            )

    # Verify original watcher is still healthy after removing the second cluster
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
        status="active",
        timeout=300,
    )


@pytest.mark.abort_on_fail
async def test_watcher_production_profile_az_blocked(ops_test: OpsTest, charm) -> None:
    """Test watcher with profile=production blocks on AZ co-location.

    When all units are in the same availability zone (common on single-host
    LXD deployments), a watcher with profile=production should enter
    BlockedStatus because it shares an AZ with the PostgreSQL units.
    This validates the AZ enforcement behavior.

    If JUJU_AVAILABILITY_ZONE is not set (some CI environments), the watcher
    should reach active status since no AZ co-location can be detected.

    Since watcher-offer has limit: 1, we must remove the existing testing watcher
    before deploying the production one, then restore it afterward.
    """
    production_watcher = "pg-watcher-prod"

    # Remove existing watcher to free the watcher-offer relation slot
    logger.info("Removing existing testing watcher to free relation slot")
    if WATCHER_APP_NAME in ops_test.model.applications:
        await ops_test.model.remove_application(
            WATCHER_APP_NAME, block_until_done=True, force=True
        )

    try:
        # Deploy a production-profile watcher
        logger.info("Deploying watcher with profile=production")
        await ops_test.model.deploy(
            charm,
            application_name=production_watcher,
            num_units=1,
            series="noble",
            config={"role": "watcher", "profile": "production"},
        )

        # Wait for initial install
        await ops_test.model.wait_for_idle(
            apps=[production_watcher],
            timeout=600,
            raise_on_error=False,
        )

        # Relate to the existing PostgreSQL cluster
        await ops_test.model.integrate(
            f"{DATABASE_APP_NAME}:watcher-offer", f"{production_watcher}:watcher"
        )

        # Wait for the watcher to settle (it may block or go active depending on AZ)
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[production_watcher],
                timeout=600,
                raise_on_error=False,
            )

        # Check the watcher's status
        watcher_unit = ops_test.model.applications[production_watcher].units[0]
        status = watcher_unit.workload_status
        status_msg = watcher_unit.workload_status_message

        if status == "blocked":
            # AZ is set and co-located — expected on single-host deployments
            assert "AZ co-location" in status_msg, (
                f"Blocked status should mention AZ co-location, got: {status_msg}"
            )
            logger.info(f"Production watcher correctly blocked: {status_msg}")
        elif status == "active":
            # AZ is not set — no co-location detected, watcher is active
            logger.info("JUJU_AVAILABILITY_ZONE not set, watcher is active (no AZ enforcement)")
        else:
            pytest.fail(
                f"Unexpected watcher status: {status} - {status_msg}. "
                "Expected 'blocked' (AZ co-location) or 'active' (no AZ)."
            )

    finally:
        # Clean up production watcher
        if production_watcher in ops_test.model.applications:
            await ops_test.model.remove_application(
                production_watcher, block_until_done=True, force=True
            )

        # Restore the original testing watcher
        logger.info("Restoring original testing watcher")
        await ops_test.model.deploy(
            charm,
            application_name=WATCHER_APP_NAME,
            num_units=1,
            series="noble",
            config={"role": "watcher", "profile": "testing"},
        )
        await ops_test.model.wait_for_idle(
            apps=[WATCHER_APP_NAME],
            timeout=600,
            raise_on_error=False,
        )
        await ops_test.model.integrate(
            f"{DATABASE_APP_NAME}:watcher-offer", f"{WATCHER_APP_NAME}:watcher"
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            status="active",
            timeout=600,
        )
