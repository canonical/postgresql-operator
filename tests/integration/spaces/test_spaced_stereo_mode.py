#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for PostgreSQL stereo mode with Juju spaces.

Verifies that stereo mode works when PostgreSQL and the watcher are
deployed in separate Juju spaces. The watcher-offer/watcher relation
must work across space boundaries for Raft consensus.

Sets up its own LXD networks and Juju spaces (does not depend on the
jubilant-based conftest fixtures).
"""

import logging
import subprocess

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..ha_tests.helpers import APPLICATION_NAME as TEST_APP_NAME
from ..ha_tests.test_stereo_mode import (
    start_writes,
    verify_raft_cluster_health,
)
from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    DATABASE_APP_NAME,
)

logger = logging.getLogger(__name__)


async def get_cluster_roles_via_exec(ops_test: OpsTest, unit_name: str) -> dict[str, list[str]]:
    """Get Patroni cluster roles by querying the API from inside the unit.

    Uses the Patroni REST API address from the Patroni config file, since
    with Juju spaces Patroni binds to a space-specific IP (not localhost).
    """
    import json

    # Get the Patroni REST API address from config (bound to pg-space IP)
    return_code, stdout, _ = await ops_test.juju(
        "exec",
        "--unit",
        unit_name,
        "--",
        "bash",
        "-c",
        "grep 'connect_address' /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml"
        " | head -1 | awk '{print $2}' | tr -d \"'\"",
    )
    assert return_code == 0, f"Failed to get Patroni REST address on {unit_name}"
    patroni_addr = stdout.strip()
    logger.info(f"Patroni REST API on {unit_name}: {patroni_addr}")

    return_code, stdout, stderr = await ops_test.juju(
        "exec",
        "--unit",
        unit_name,
        "--",
        "curl",
        "-sk",
        f"https://{patroni_addr}/cluster",
    )
    assert return_code == 0, (
        f"Failed to query Patroni cluster on {unit_name}: "
        f"rc={return_code}, stdout={stdout!r}, stderr={stderr!r}"
    )

    members: dict[str, list[str]] = {"replicas": [], "primaries": [], "sync_standbys": []}
    cluster_info = json.loads(stdout)
    logger.info(f"Cluster members on {unit_name}: {cluster_info.get('members', [])}")
    for member in cluster_info["members"]:
        role = member["role"]
        name = "/".join(member["name"].rsplit("-", 1))
        if role == "leader":
            members["primaries"].append(name)
        elif role == "sync_standby":
            members["sync_standbys"].append(name)
        else:
            members["replicas"].append(name)
    return members


WATCHER_APP_NAME = "pg-watcher"

# LXD networks: pg-space for PostgreSQL, watcher-space for the watcher
NETWORKS = {
    "pg-space": "10.40.40.1/24",
    "watcher-space": "10.50.50.1/24",
}

DEFAULT_LXD_NETWORK = "lxdbr0"


def _create_lxd_network(name: str, subnet: str) -> None:
    """Create an LXD bridge network."""
    try:
        subprocess.run(
            [
                "sudo",
                "lxc",
                "network",
                "create",
                name,
                "--type=bridge",
                f"ipv4.address={subnet}",
                "ipv4.nat=true",
                "ipv6.address=none",
                "dns.mode=none",
            ],
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        subprocess.check_output(f"sudo ip link set up dev {name}".split())
        logger.info(f"Created LXD network {name} with subnet {subnet}")
    except subprocess.CalledProcessError as e:
        if "The network already exists" in (e.stderr or ""):
            logger.warning(f"LXD network {name} already exists")
        else:
            raise


@pytest.fixture(scope="module")
def lxd_networks():
    """Create LXD networks for the two spaces."""
    # Set dns.mode=none on default network to avoid DNS conflicts
    subprocess.run(
        ["sudo", "lxc", "network", "set", DEFAULT_LXD_NETWORK, "dns.mode=none"],
        check=True,
    )

    for name, subnet in NETWORKS.items():
        _create_lxd_network(name, subnet)

    yield

    for name in NETWORKS:
        try:
            subprocess.check_output(f"sudo lxc network delete {name}".split())
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to delete LXD network {name}")

    try:
        subprocess.check_output(f"sudo lxc network unset {DEFAULT_LXD_NETWORK} dns.mode".split())
    except subprocess.CalledProcessError:
        logger.warning("Failed to restore dns.mode on default network")


@pytest.fixture(scope="module")
async def spaced_model(ops_test: OpsTest, lxd_networks):
    """Set up Juju spaces for the test model."""
    await ops_test.juju("reload-spaces")

    for name, subnet in NETWORKS.items():
        try:
            await ops_test.juju("add-space", name, subnet)
        except Exception as e:
            if "already exists" in str(e):
                logger.info(f"Space {name} already exists")
            else:
                raise

    logger.info(f"Juju spaces configured: {', '.join(NETWORKS)}")


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Fixture to clean up continuous writes after each test."""
    yield
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await ops_test.model
                .applications[TEST_APP_NAME]
                .units[0]
                .run_action("clear-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to clear up continuous_writes table"


@pytest.mark.abort_on_fail
async def test_deploy_stereo_mode_with_spaces(ops_test: OpsTest, charm, spaced_model) -> None:
    """Deploy stereo mode with PostgreSQL and watcher in separate Juju spaces.

    - PostgreSQL units: deployed with spaces=pg-space
    - Watcher unit: deployed with spaces=watcher-space
    - The watcher-offer/watcher relation bridges the two spaces
    """
    if DATABASE_APP_NAME in ops_test.model.applications:
        pg_units = len(ops_test.model.applications[DATABASE_APP_NAME].units)
        watcher_deployed = WATCHER_APP_NAME in ops_test.model.applications
        test_app_deployed = APPLICATION_NAME in ops_test.model.applications

        if pg_units == 2 and watcher_deployed and test_app_deployed:
            logger.info("Stereo mode already deployed, verifying...")
            await ops_test.model.wait_for_idle(status="active", timeout=300)
            return

        for app in [DATABASE_APP_NAME, WATCHER_APP_NAME, APPLICATION_NAME]:
            if app in ops_test.model.applications:
                await ops_test.model.remove_application(app, block_until_done=True)

    async with ops_test.fast_forward():
        # Deploy PostgreSQL: peers + database on pg-space, watcher relation on watcher-space
        logger.info("Deploying PostgreSQL with pg-space + watcher-space...")
        await ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=2,
            base=CHARM_BASE,
            config={"profile": "testing"},
            constraints={"spaces": ["pg-space", "watcher-space"]},
            bind={
                "database-peers": "pg-space",
                "database": "pg-space",
                "watcher-offer": "watcher-space",
            },
        )

        # Deploy watcher: all traffic on watcher-space
        logger.info("Deploying watcher with spaces=watcher-space...")
        await ops_test.model.deploy(
            charm,
            application_name=WATCHER_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
            config={"role": "watcher", "profile": "testing"},
            constraints={"spaces": ["watcher-space"]},
            bind={"watcher": "watcher-space"},
        )

        # Deploy test app in pg-space
        logger.info("Deploying test application with spaces=pg-space...")
        await ops_test.model.deploy(
            APPLICATION_NAME,
            application_name=APPLICATION_NAME,
            base=CHARM_BASE,
            channel="edge",
            constraints={"spaces": ["pg-space"]},
            bind={"database": "pg-space"},
        )

        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            timeout=1200,
            raise_on_error=False,
        )

        # Relate PostgreSQL to watcher across spaces
        logger.info("Relating PostgreSQL to watcher (cross-space)")
        try:
            await ops_test.model.integrate(
                f"{DATABASE_APP_NAME}:watcher-offer", f"{WATCHER_APP_NAME}:watcher"
            )
        except Exception as e:
            if "already exists" in str(e):
                logger.info(f"Watcher relation already exists: {e}")
            else:
                raise

        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
            status="active",
            timeout=600,
        )

        # Relate PostgreSQL to test app
        try:
            await ops_test.model.integrate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        except Exception as e:
            if "already exists" in str(e):
                logger.info(f"Database relation already exists: {e}")
            else:
                raise

        await ops_test.model.wait_for_idle(status="active", timeout=1800)

    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 2
    assert len(ops_test.model.applications[WATCHER_APP_NAME].units) == 1


@pytest.mark.abort_on_fail
async def test_raft_quorum_across_spaces(ops_test: OpsTest) -> None:
    """Verify Raft quorum is established across spaces."""
    # check_watcher_ip=False because the watcher's Raft address is on
    # watcher-space, not the default address returned by unit-get private-address
    await verify_raft_cluster_health(
        ops_test, DATABASE_APP_NAME, WATCHER_APP_NAME, check_watcher_ip=False
    )


@pytest.mark.abort_on_fail
async def test_topology_action_with_spaces(ops_test: OpsTest) -> None:
    """Test get-cluster-status action returns correct cross-space topology."""
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]

    action = await watcher_unit.run_action("get-cluster-status")
    action = await action.wait()

    assert action.status == "completed"
    assert "status" in action.results

    import json

    status = json.loads(action.results["status"])
    # Single cluster: status is the cluster dict directly
    assert "clustername" in status
    assert "topology" in status
    # Topology should have 2 PG units + 1 watcher = 3 entries
    assert len(status["topology"]) == 3


@pytest.mark.abort_on_fail
async def test_primary_shutdown_failover_across_spaces(
    ops_test: OpsTest, continuous_writes
) -> None:
    """Test primary shutdown triggers failover with watcher in a separate space.

    This is the critical test: the watcher must provide the Raft vote
    across the space boundary for failover to succeed.
    """
    await start_writes(ops_test)

    #  because Patroni API is bound to pg-space,
    # not the default address that python-libjuju returns
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles_via_exec(ops_test, any_unit)
    original_primary = original_roles["primaries"][0]

    if original_roles["sync_standbys"]:
        original_replica = original_roles["sync_standbys"][0]
    else:
        original_replica = None
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            if unit.name != original_primary:
                original_replica = unit.name
                break
        assert original_replica is not None

    logger.info(f"Shutting down primary: {original_primary}")

    await ops_test.model.destroy_unit(
        original_primary, force=True, destroy_storage=False, max_wait=1500
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=600,
        idle_period=30,
    )

    # Verify failover happened — watcher's Raft vote across spaces enabled this
    remaining_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    for attempt in Retrying(stop=stop_after_delay(180), wait=wait_fixed(10), reraise=True):
        with attempt:
            new_roles = await get_cluster_roles_via_exec(ops_test, remaining_unit)
            logger.info(f"Post-failover roles: {new_roles}")
            assert len(new_roles["primaries"]) == 1
            assert new_roles["primaries"][0] == original_replica

    # Scale back up
    logger.info("Scaling back up after primary shutdown")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=1800, idle_period=60)

    for attempt in Retrying(stop=stop_after_delay(300), wait=wait_fixed(15), reraise=True):
        with attempt:
            final_roles = await get_cluster_roles_via_exec(
                ops_test,
                ops_test.model.applications[DATABASE_APP_NAME].units[0].name,
            )
            assert len(final_roles["primaries"]) == 1
            assert len(final_roles["sync_standbys"]) == 1

    logger.info("Failover verified — watcher Raft vote worked across spaces")


@pytest.mark.abort_on_fail
async def test_watcher_shutdown_across_spaces(ops_test: OpsTest, continuous_writes) -> None:
    """Test watcher shutdown — no outage even when watcher is in a different space."""
    any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    original_roles = await get_cluster_roles_via_exec(ops_test, any_unit)

    logger.info("Removing watcher unit (separate space)")
    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]
    await ops_test.model.destroy_unit(watcher_unit.name, force=True, max_wait=300)

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=300,
        idle_period=30,
    )

    new_roles = await get_cluster_roles_via_exec(ops_test, any_unit)
    assert new_roles["primaries"] == original_roles["primaries"]

    # Re-deploy watcher in the watcher space
    logger.info("Re-deploying watcher in watcher-space")
    await ops_test.model.applications[WATCHER_APP_NAME].add_unit(count=1)
    await ops_test.model.wait_for_idle(status="active", timeout=600)

    await verify_raft_cluster_health(
        ops_test, DATABASE_APP_NAME, WATCHER_APP_NAME, check_watcher_ip=False
    )


@pytest.mark.abort_on_fail
async def test_health_check_across_spaces(ops_test: OpsTest) -> None:
    """Test health check action works across space boundaries."""
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, WATCHER_APP_NAME],
        status="active",
        timeout=300,
        idle_period=30,
    )

    await verify_raft_cluster_health(
        ops_test,
        DATABASE_APP_NAME,
        WATCHER_APP_NAME,
        expected_members=3,
        check_watcher_ip=False,
    )

    watcher_unit = ops_test.model.applications[WATCHER_APP_NAME].units[0]

    for attempt in Retrying(stop=stop_after_delay(360), wait=wait_fixed(10), reraise=True):
        with attempt:
            action = await watcher_unit.run_action("trigger-health-check")
            action = await action.wait()

            assert action.status == "completed", f"Action failed: {action.results}"
            assert "health-check" in action.results

            import json

            health = json.loads(action.results["health-check"])
            assert "clusters" in health
            assert int(health["healthy-count"]) == 2
            assert int(health["total-count"]) == 2
