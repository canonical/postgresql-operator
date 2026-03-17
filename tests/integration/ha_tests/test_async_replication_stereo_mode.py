#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for async replication with stereo mode watcher.

Verifies that a single watcher can serve as the third Raft node for both
a primary and a standby PostgreSQL cluster simultaneously, while async
replication is active between them.
"""

import logging

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
)
from .test_stereo_mode import (
    verify_raft_cluster_health,
)

logger = logging.getLogger(__name__)

PRIMARY_APP = DATABASE_APP_NAME  # "postgresql"
STANDBY_APP = "postgresql-standby"
WATCHER_APP = "pg-watcher"


@pytest.mark.abort_on_fail
async def test_deploy_async_replication_with_watcher(ops_test: OpsTest, charm) -> None:
    """Deploy two PG clusters with a shared watcher and async replication.

    Architecture:
    - Primary cluster (2 units) + watcher = 3 Raft members
    - Standby cluster (2 units) + watcher = 3 Raft members
    - Async replication: primary → standby
    """
    async with ops_test.fast_forward():
        # Deploy primary cluster
        logger.info("Deploying primary cluster (2 units)...")
        await ops_test.model.deploy(
            charm,
            application_name=PRIMARY_APP,
            num_units=2,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )

        # Deploy standby cluster
        logger.info("Deploying standby cluster (2 units)...")
        await ops_test.model.deploy(
            charm,
            application_name=STANDBY_APP,
            num_units=2,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )

        # Deploy watcher (single instance for both clusters)
        logger.info("Deploying watcher (shared by both clusters)...")
        await ops_test.model.deploy(
            charm,
            application_name=WATCHER_APP,
            num_units=1,
            base=CHARM_BASE,
            config={"role": "watcher", "profile": "testing"},
        )

        # Wait for all apps to settle
        await ops_test.model.wait_for_idle(
            apps=[PRIMARY_APP, STANDBY_APP, WATCHER_APP],
            timeout=1200,
            raise_on_error=False,
        )

        # Relate watcher to primary cluster
        logger.info("Relating watcher to primary cluster")
        await ops_test.model.integrate(f"{PRIMARY_APP}:watcher-offer", f"{WATCHER_APP}:watcher")

        # Relate watcher to standby cluster
        logger.info("Relating watcher to standby cluster")
        await ops_test.model.integrate(f"{STANDBY_APP}:watcher-offer", f"{WATCHER_APP}:watcher")

        # Wait for watcher to join both Raft clusters
        await ops_test.model.wait_for_idle(
            apps=[PRIMARY_APP, STANDBY_APP, WATCHER_APP],
            status="active",
            timeout=600,
        )

    # Verify deployment
    assert len(ops_test.model.applications[PRIMARY_APP].units) == 2
    assert len(ops_test.model.applications[STANDBY_APP].units) == 2
    assert len(ops_test.model.applications[WATCHER_APP].units) == 1


@pytest.mark.abort_on_fail
async def test_watcher_raft_quorum_both_clusters(ops_test: OpsTest) -> None:
    """Verify the watcher has Raft quorum in both clusters."""
    # Check primary cluster Raft
    logger.info("Verifying Raft quorum in primary cluster")
    await verify_raft_cluster_health(ops_test, PRIMARY_APP, WATCHER_APP)

    # Check standby cluster Raft
    logger.info("Verifying Raft quorum in standby cluster")
    await verify_raft_cluster_health(ops_test, STANDBY_APP, WATCHER_APP)


@pytest.mark.abort_on_fail
async def test_watcher_topology_shows_both_clusters(ops_test: OpsTest) -> None:
    """Verify show-topology action reports both clusters."""
    import json

    watcher_unit = ops_test.model.applications[WATCHER_APP].units[0]
    action = await watcher_unit.run_action("show-topology")
    action = await action.wait()

    assert action.status == "completed"
    topology = json.loads(action.results["topology"])
    assert len(topology["clusters"]) == 2, f"Expected 2 clusters, got {len(topology['clusters'])}"

    cluster_names = sorted(c["cluster_name"] for c in topology["clusters"])
    logger.info(f"Watcher sees clusters: {cluster_names}")

    # Each cluster should have 2 endpoints
    for cluster in topology["clusters"]:
        assert len(cluster["postgresql_endpoints"]) == 2, (
            f"Cluster {cluster['cluster_name']} should have 2 endpoints"
        )


@pytest.mark.abort_on_fail
async def test_setup_async_replication(ops_test: OpsTest) -> None:
    """Set up async replication from primary to standby cluster."""
    # Relate the two clusters for async replication
    logger.info("Setting up async replication: primary → standby")
    await ops_test.model.integrate(
        f"{PRIMARY_APP}:replication-offer", f"{STANDBY_APP}:replication"
    )

    # Wait for relation to be established
    await ops_test.model.wait_for_idle(
        apps=[PRIMARY_APP, STANDBY_APP],
        timeout=600,
        raise_on_error=False,
    )

    # Run create-replication action on primary leader
    primary_leader = None
    for unit in ops_test.model.applications[PRIMARY_APP].units:
        if await unit.is_leader_from_status():
            primary_leader = unit
            break
    assert primary_leader is not None, "Could not find primary cluster leader"

    logger.info(f"Running create-replication on {primary_leader.name}")
    action = await primary_leader.run_action("create-replication")
    action = await action.wait()
    logger.info(f"create-replication result: {action.status} - {action.results}")

    # Wait for replication to be established
    # The standby cluster should transition to standby mode
    await ops_test.model.wait_for_idle(
        apps=[PRIMARY_APP, STANDBY_APP],
        timeout=900,
        raise_on_error=False,
    )

    # Verify the standby units show as replicas
    for attempt in Retrying(stop=stop_after_delay(300), wait=wait_fixed(15), reraise=True):
        with attempt:
            standby_status = ops_test.model.applications[STANDBY_APP].status
            logger.info(f"Standby cluster status: {standby_status}")
            # Standby should be active (as a standby cluster)
            assert standby_status == "active", (
                f"Standby cluster should be active, got {standby_status}"
            )


@pytest.mark.abort_on_fail
async def test_watcher_quorum_after_replication(ops_test: OpsTest) -> None:
    """Verify watcher maintains Raft quorum in the primary cluster after replication.

    After create-replication, the standby cluster's Patroni restarts to
    follow the primary, which temporarily disrupts its Raft cluster.
    We verify the primary cluster's Raft is unaffected and that the
    watcher still reports both clusters in its topology.
    """
    # Give the standby cluster time to stabilize after replication setup
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[PRIMARY_APP, STANDBY_APP, WATCHER_APP],
            timeout=600,
            raise_on_error=False,
        )

    # Primary cluster Raft should be unaffected by standby replication setup
    logger.info("Verifying Raft quorum in primary cluster (post-replication)")
    await verify_raft_cluster_health(ops_test, PRIMARY_APP, WATCHER_APP)

    # Verify the watcher still reports both clusters in topology
    import json

    watcher_unit = ops_test.model.applications[WATCHER_APP].units[0]
    action = await watcher_unit.run_action("show-topology")
    action = await action.wait()
    assert action.status == "completed"
    topology = json.loads(action.results["topology"])
    assert len(topology["clusters"]) == 2, (
        f"Watcher should still see 2 clusters after replication, got {len(topology['clusters'])}"
    )
    logger.info("Watcher still monitors both clusters after replication setup")


@pytest.mark.abort_on_fail
async def test_health_check_both_clusters(ops_test: OpsTest) -> None:
    """Verify health check action reports both clusters.

    After create-replication, the standby cluster runs in standby mode.
    The watcher health check connects to all endpoints, but standby
    endpoints may have different connection behavior. We verify the
    action completes and reports both clusters with at least the
    primary cluster's endpoints healthy.
    """
    import json

    watcher_unit = ops_test.model.applications[WATCHER_APP].units[0]

    for attempt in Retrying(stop=stop_after_delay(360), wait=wait_fixed(10), reraise=True):
        with attempt:
            action = await watcher_unit.run_action("trigger-health-check")
            action = await action.wait()

            assert action.status == "completed", f"Action failed: {action.results}"
            health = json.loads(action.results["health-check"])
            assert len(health["clusters"]) == 2, (
                f"Expected 2 clusters in health check, got {len(health['clusters'])}"
            )
            assert int(health["total-count"]) == 4, (
                f"Expected 4 total endpoints, got {health['total-count']}"
            )
            # Primary cluster (2 endpoints) should be healthy;
            # standby cluster may or may not respond to SELECT 1
            assert int(health["healthy-count"]) >= 2, (
                f"Expected at least 2 healthy endpoints (primary cluster), "
                f"got {health['healthy-count']}"
            )

    logger.info(
        f"Health check: {health['healthy-count']}/{health['total-count']} "
        f"endpoints healthy across 2 clusters"
    )
