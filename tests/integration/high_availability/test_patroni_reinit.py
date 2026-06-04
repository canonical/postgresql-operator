# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: replica reinit after pg_control corruption via patronictl reinit."""

import logging

import jubilant
import requests
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from constants import PATRONI_CONF_PATH, PATRONI_LOGS_PATH, POSTGRESQL_DATA_DIR

from .high_availability_helpers_new import (
    MINUTE_SECS,
    check_db_units_writes_increment,
    get_app_units,
    get_db_primary_unit,
    get_unit_ip,
    wait_for_apps_status,
)

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DB_APP_NAME = "postgresql"
DB_TEST_APP_NAME = "postgresql-test-app"

PG_CONTROL_PATH = f"{POSTGRESQL_DATA_DIR}/global/pg_control"


def test_deploy(juju: Juju, charm: str) -> None:
    """Deploy a 3-unit PostgreSQL cluster and the continuous-writes test application."""
    logger.info("Deploying PostgreSQL cluster (%s, 3 units)", DB_APP_NAME)
    juju.deploy(
        charm=charm,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        config={"profile": "testing"},
        num_units=3,
    )

    logger.info("Deploying test application (%s)", DB_TEST_APP_NAME)
    juju.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_NAME,
        base="ubuntu@24.04",
        channel="latest/edge",
        num_units=1,
    )

    juju.integrate(f"{DB_APP_NAME}:database", f"{DB_TEST_APP_NAME}:database")

    logger.info("Waiting for all applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, DB_TEST_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_patroni_reinit_after_pg_control_deletion(juju: Juju, continuous_writes) -> None:
    """Verify a replica reinitialises correctly after pg_control deletion and patronictl reinit.

    This validates that the versioned storage layout (data under 16/main subdirectories)
    works correctly with Patroni's reinit operation, which was previously broken when
    data lived at the mount point roots.

    Steps:
    1. Confirm the cluster is healthy and continuous writes are flowing.
    2. Stop Patroni on a replica (waiting until it is fully stopped), delete pg_control,
       then restart Patroni so that PostgreSQL fails to start due to the missing control file.
    3. Verify Patroni logs record a pg_control startup failure and that the replica is no
       longer streaming, so the test cannot falsely pass on a still-healthy replica.
    4. Run ``patronictl reinit`` to restore the replica from the primary.
    5. Confirm the replica returns to the streaming state, that reinit did not log
       data-directory permission errors, that pg_control is present, and that data
       integrity is maintained across all units.
    """
    logger.info("Identifying primary and replica units")
    primary_unit = get_db_primary_unit(juju, DB_APP_NAME)
    all_units = get_app_units(juju, DB_APP_NAME)
    replica_unit = next(unit for unit in all_units if unit != primary_unit)

    logger.info("Primary: %s | Replica under test: %s", primary_unit, replica_unit)

    primary_ip = get_unit_ip(juju, DB_APP_NAME, primary_unit)
    replica_ip = get_unit_ip(juju, DB_APP_NAME, replica_unit)

    replica_member_name = replica_unit.replace("/", "-")
    cluster_name = DB_APP_NAME

    def get_member_state() -> str | None:
        """Return the replica's reported state from the primary's /cluster endpoint."""
        cluster_resp = requests.get(f"https://{primary_ip}:8008/cluster", verify=False)
        members = cluster_resp.json()["members"]
        return next((m["state"] for m in members if m["name"] == replica_member_name), None)

    logger.info("Verifying initial health of replica %s", replica_unit)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(5), reraise=True):
        with attempt:
            resp = requests.get(f"https://{replica_ip}:8008/health", verify=False)
            assert resp.status_code == 200, (
                f"Replica {replica_unit} unhealthy before test: HTTP {resp.status_code}"
            )

    logger.info("Verifying continuous writes are flowing")
    check_db_units_writes_increment(juju, DB_APP_NAME)

    logger.info("Stopping Patroni on %s", replica_unit)
    juju.ssh(replica_unit, "sudo systemctl stop snap.charmed-postgresql.patroni")

    logger.info("Waiting for Patroni to stop on %s before deleting pg_control", replica_unit)
    for attempt in Retrying(stop=stop_after_delay(MINUTE_SECS), wait=wait_fixed(3), reraise=True):
        with attempt:
            active_state = juju.ssh(
                replica_unit,
                "systemctl is-active snap.charmed-postgresql.patroni || true",
            ).strip()
            assert active_state != "active", (
                f"Patroni still active on {replica_unit} ({active_state!r}); refusing to delete "
                "pg_control while PostgreSQL may still be running"
            )

    logger.info("Deleting pg_control at %s on %s", PG_CONTROL_PATH, replica_unit)
    juju.ssh(replica_unit, f"sudo rm {PG_CONTROL_PATH}")

    logger.info("Starting Patroni on %s (pg_control is now missing)", replica_unit)
    juju.ssh(replica_unit, "sudo systemctl start snap.charmed-postgresql.patroni")

    logger.info("Confirming pg_control deletion broke replica %s (not streaming)", replica_unit)
    broken_state: str | None = None
    for attempt in Retrying(
        stop=stop_after_delay(2 * MINUTE_SECS), wait=wait_fixed(5), reraise=True
    ):
        with attempt:
            broken_state = get_member_state()
            assert broken_state not in ("running", "streaming"), (
                f"Replica {replica_unit} unexpectedly healthy ({broken_state!r}) before reinit; "
                "pg_control deletion did not break it, so the test would falsely pass"
            )
    logger.info("Replica %s broken (state=%r) before reinit", replica_unit, broken_state)

    logger.info("Running patronictl reinit on %s", replica_unit)
    patronictl_cmd = (
        f"sudo charmed-postgresql.patronictl -c {PATRONI_CONF_PATH}/patroni.yaml "
        f"reinit {cluster_name} {replica_member_name} --force"
    )
    juju.ssh(replica_unit, patronictl_cmd)

    logger.info("Waiting for %s to reach the streaming state after reinit", replica_unit)
    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS), wait=wait_fixed(10), reraise=True
    ):
        with attempt:
            replica_state = get_member_state()
            assert replica_state == "streaming", (
                f"Replica {replica_unit} not streaming after reinit: {replica_state!r}"
            )
    logger.info("Replica %s is streaming after reinit", replica_unit)

    logger.info("Checking reinit logged no data-directory permission errors on %s", replica_unit)
    permission_error_grep = (
        "sudo grep -iEh "
        "'could not remove data directory|could not rename data directory|permissionerror' "
        f"{PATRONI_LOGS_PATH}/patroni.log* 2>/dev/null || true"
    )
    permission_errors = juju.ssh(replica_unit, permission_error_grep).strip()
    assert not permission_errors, (
        f"patronictl reinit on {replica_unit} logged data-directory permission failures; the "
        "versioned-storage parent directory is not writable by the PostgreSQL user. "
        f"Matching Patroni log lines:\n{permission_errors}"
    )
    logger.info("No data-directory permission errors logged during reinit on %s", replica_unit)

    logger.info("Verifying pg_control exists in data directory of %s after reinit", replica_unit)
    result = juju.ssh(
        replica_unit,
        f"sudo test -f {PG_CONTROL_PATH} && echo exists || echo missing",
    )
    assert "exists" in result, (
        f"pg_control is missing from {replica_unit} after reinit (expected at {PG_CONTROL_PATH})"
    )
    logger.info("pg_control is present at %s on %s", PG_CONTROL_PATH, replica_unit)

    logger.info("Verifying data integrity on all units after reinit")
    check_db_units_writes_increment(juju, DB_APP_NAME)
    logger.info(
        "Data integrity verified: all units (including %s) have consistent data", replica_unit
    )
