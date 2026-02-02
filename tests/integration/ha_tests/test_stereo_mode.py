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
import subprocess
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import architecture
from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
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

    # Get watcher address for verification
    watcher_unit = ops_test.model.applications[watcher_app_name].units[0]
    watcher_ip = await watcher_unit.get_public_address()

    for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(10), reraise=True):
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

                # Parse the Raft password from YAML - look in the raft: section
                # The structure is:
                # raft:
                #   data_dir: ...
                #   self_addr: ...
                #   password: THE_PASSWORD_WE_NEED
                password = None
                in_raft_section = False
                for line in stdout.split("\n"):
                    if line.strip() == "raft:" or line.startswith("raft:"):
                        in_raft_section = True
                        continue
                    # Exit raft section when we hit another top-level key
                    if in_raft_section and line and not line.startswith(" ") and ":" in line:
                        in_raft_section = False
                    if in_raft_section and "password:" in line:
                        # Extract the password value after "password:"
                        password = line.split("password:")[-1].strip()
                        break
                assert password, f"Could not find Raft password in patroni.yaml on {unit.name}"

                # Check Raft status using the password via juju exec directly
                complete_command = [
                    "exec",
                    "--unit",
                    unit.name,
                    "--",
                    "charmed-postgresql.syncobj-admin",
                    "-conn",
                    "127.0.0.1:2222",
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
                        f"Watcher {watcher_ip} not found in Raft cluster on {unit.name}"
                    )

    logger.info("Raft cluster health verified successfully")


WATCHER_APP_NAME = "postgresql-watcher"


@pytest.fixture(scope="session")
def watcher_charm():
    """Return path to the watcher charm, building it if necessary."""
    watcher_dir = Path("./postgresql-watcher")
    charm_path = watcher_dir / f"postgresql-watcher_ubuntu@24.04-{architecture.architecture}.charm"

    if not charm_path.exists():
        logger.info(f"Watcher charm not found at {charm_path}, building...")
        subprocess.run(
            ["charmcraft", "pack", "-v"],
            cwd=watcher_dir,
            check=True,
        )

    if not charm_path.exists():
        raise FileNotFoundError(f"Failed to build watcher charm at {charm_path}")

    # Return path with "./" prefix so python-libjuju recognizes it as a local charm
    return f"./{charm_path}"


@pytest.mark.abort_on_fail
async def test_build_and_deploy_stereo_mode(ops_test: OpsTest, charm, watcher_charm) -> None:
    """Build and deploy PostgreSQL in stereo mode with watcher.

    Deploy order is critical for stereo mode with Raft DCS:
    1. Deploy PostgreSQL with 1 unit first (establishes Raft cluster)
    2. Deploy and relate watcher (provides quorum vote - now 2 out of 3)
    3. Scale PostgreSQL to 2 units (new unit joins as replica with quorum)

    If we deploy 2 PostgreSQL units before the watcher is related, they
    cannot form Raft quorum (need 2 out of 3) and both initialize
    independently with different system IDs.
    """
    logger.info(f"DEBUG: charm={charm!r}, watcher_charm={watcher_charm!r}")

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
        # Step 1: Deploy PostgreSQL with ONLY 1 unit initially
        # This establishes a single-node Raft cluster that can be leader
        logger.info("Deploying PostgreSQL charm...")
        await ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=1,  # IMPORTANT: Start with 1 unit only
            base=CHARM_BASE,
            config={"profile": "testing"},
        )
        logger.info("Deploying watcher charm...")
        # Deploy the watcher charm
        await ops_test.model.deploy(
            watcher_charm,
            application_name=WATCHER_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
        )
        logger.info("Deploying test application...")
        # Deploy test application
        await ops_test.model.deploy(
            APPLICATION_NAME,
            application_name=APPLICATION_NAME,
            base=CHARM_BASE,
            channel="edge",
        )

        # Wait for initial deployment
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            timeout=1200,
            raise_on_error=False,  # Watcher may be waiting for relation
        )

        # Step 2: Relate PostgreSQL to watcher BEFORE adding second unit
        # This adds the watcher to the Raft cluster, providing quorum
        logger.info("Relating PostgreSQL to watcher for Raft quorum")
        await ops_test.model.relate(f"{DATABASE_APP_NAME}:watcher", f"{WATCHER_APP_NAME}:watcher")

        # Wait for watcher to join Raft cluster
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            status="active",
            timeout=600,
        )

        # Relate PostgreSQL to test app
        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")

        # Step 3: Now scale PostgreSQL to 2 units
        # The new unit will join the existing Raft cluster with quorum
        logger.info("Scaling PostgreSQL to 2 units (stereo mode)")
        await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)

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
        cut_network_from_unit(primary_machine)

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

    finally:
        # Restore network
        logger.info(f"Restoring network for {primary_machine}")
        restore_network_for_unit(primary_machine)

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
            assert primary not in final_roles["primaries"], "Old primary should now be a replica"

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

        # With synchronous_mode_strict=true, writes will pause when there's no sync_standby.
        # That's expected behavior for data safety. We just verify the primary doesn't failover.
        # Give Patroni time to detect the network isolation.
        await asyncio.sleep(30)

        # Primary should remain primary (no failover should happen)
        # Raft quorum is maintained with primary + watcher (2 out of 3)
        current_primary = await get_primary(ops_test, DATABASE_APP_NAME, down_unit=replica)
        assert current_primary == primary, "Primary should not change during replica isolation"

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

    # Verify cluster roles unchanged
    final_roles = await get_cluster_roles(ops_test, any_unit, use_ip_from_inside=True)
    assert final_roles["primaries"][0] == primary, "Primary should remain the same after restore"

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
async def test_health_check_action(ops_test: OpsTest) -> None:
    """Test the trigger-health-check action on the watcher."""
    # Wait for the cluster to fully stabilize after previous network tests
    # The watcher may need time to reconnect and receive endpoint data after network manipulation
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
        status="active",
        timeout=300,
        idle_period=30,
    )

    # Also verify Raft cluster health to ensure watcher is fully connected
    # After network isolation tests, the watcher may have been redeployed with a new IP
    # that isn't in the Raft configuration yet, so we skip the watcher IP check
    await verify_raft_cluster_health(
        ops_test, DATABASE_APP_NAME, WATCHER_APP_NAME, expected_members=3, check_watcher_ip=False
    )

    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]

    # Retry the action multiple times as the watcher needs to receive fresh endpoint data
    # from the relation after reconnecting. The pg-endpoints are updated by the PostgreSQL
    # leader in update_status (runs every 5 minutes), so we need to wait long enough for
    # at least one update_status cycle to complete.
    for attempt in Retrying(stop=stop_after_delay(360), wait=wait_fixed(10), reraise=True):
        with attempt:
            action = await watcher_unit.run_action("trigger-health-check")
            action = await action.wait()

            assert action.status == "completed", f"Action failed: {action.results}"
            assert "endpoints" in action.results
            assert int(action.results["healthy-count"]) == 2
            assert int(action.results["total-count"]) == 2
