# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: snap migration failure during upgrade and rollback.

Verifies charm behaviour when the snap's migrate-data.sh fails due to
filesystem permission errors:

Phase 1 — Block the versioned target directory before upgrade.  The
snap's post-refresh hook fails (set -e), leaving data in root layout
on a new snap.  After restoring permissions and resolving, the snap
hook retries or the charm recovers.

Phase 2 — Block the root target directory before rollback.  The snap's
pre-refresh hook fails, aborting the snap refresh entirely (snap stays
on the current version).  After restoring permissions the rollback can
proceed.

The number of PostgreSQL units is controlled by the NUM_UNITS environment
variable (default: 1) for spread parametrization.
"""

import logging
import os
import platform
import re
import time

import jubilant
from jubilant import Juju, TaskError

from ..helpers import execute_queries_on_unit
from .high_availability_helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_app_units,
    get_db_primary_unit,
    get_unit_ip,
    get_user_password,
    wait_for_apps_status,
)

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DB_APP_NAME = "postgresql"
DB_TEST_APP_NAME = "postgresql-test-app"
NUM_UNITS = int(os.environ.get("NUM_UNITS", "1"))

STABLE_REVISIONS = {"amd64": 1089, "arm64": 1088}

SNAP_COMMON = "/var/snap/charmed-postgresql/common"
DATA_ROOT = f"{SNAP_COMMON}/var/lib/postgresql"
TEMP_ROOT = f"{SNAP_COMMON}/data/temp"

VERSIONED_SUFFIX = "16/main"
DATA_VERSIONED = f"{DATA_ROOT}/{VERSIONED_SUFFIX}"
TEMP_VERSIONED = f"{TEMP_ROOT}/{VERSIONED_SUFFIX}"


def _get_stable_revision() -> int:
    arch = platform.machine()
    if arch == "x86_64":
        arch = "amd64"
    elif arch == "aarch64":
        arch = "arm64"
    return STABLE_REVISIONS[arch]


def _resolve_stuck_units(juju: Juju, status: jubilant.Status, unit_names: list[str]) -> None:
    """Resolve error units and retry force-refresh-start on maintenance units."""
    for unit in unit_names:
        unit_status = status.apps[DB_APP_NAME].units[unit]
        if unit_status.workload_status.current == "error":
            logger.info("Resolving %s", unit)
            try:
                juju.cli("resolve", unit)
            except Exception:
                logger.debug("resolve failed (unit may already be resolved)")
        elif unit_status.workload_status.current == "maintenance":
            logger.info("Unit %s in maintenance, retrying force-refresh-start", unit)
            try:
                juju.run(
                    unit=unit,
                    action="force-refresh-start",
                    params={"check-compatibility": False},
                    wait=10 * MINUTE_SECS,
                )
            except TaskError:
                logger.info("force-refresh-start retry also failed on %s", unit)


def _wait_for_refresh_recovery(juju: Juju, unit_names: list[str], timeout_msg: str) -> None:
    """Poll status and drive resume-refresh / resolve / force-refresh-start until active."""
    deadline = time.time() + 20 * MINUTE_SECS
    while time.time() < deadline:
        status = juju.status()
        if status.apps[DB_APP_NAME].is_active:
            break

        app_msg = status.apps[DB_APP_NAME].app_status.message or ""
        if "resume-refresh" in app_msg:
            match = re.search(r"on unit (\d+)", app_msg)
            if match:
                resume_unit = f"{DB_APP_NAME}/{match.group(1)}"
                logger.info("Running resume-refresh on %s", resume_unit)
                try:
                    juju.run(unit=resume_unit, action="resume-refresh", wait=15 * MINUTE_SECS)
                except TaskError:
                    logger.info("resume-refresh failed on %s", resume_unit)
                continue

        _resolve_stuck_units(juju, status, unit_names)
        time.sleep(30)
    else:
        raise TimeoutError(timeout_msg)


def _assert_path_exists(juju: Juju, unit: str, path: str) -> None:
    result = juju.ssh(unit, f"sudo test -e {path} && echo exists || echo missing")
    assert "exists" in result, f"Expected {path} to exist on {unit}, but it is missing"


def _assert_path_missing(juju: Juju, unit: str, path: str) -> None:
    result = juju.ssh(unit, f"sudo test -e {path} && echo exists || echo missing")
    assert "missing" in result, f"Expected {path} to be absent on {unit}, but it exists"


def _get_operator_password(juju: Juju) -> str:
    password = get_user_password(juju, DB_APP_NAME, "operator")
    assert password is not None, "Failed to retrieve operator password"
    return password


# ---- Phase 1: Deploy stable, block migration, upgrade, recover ----


def test_deploy_stable(juju: Juju) -> None:
    """Deploy PostgreSQL from a pinned 16/stable revision."""
    revision = _get_stable_revision()
    logger.info(
        "Deploying PostgreSQL from 16/stable (revision %d) with %d units", revision, NUM_UNITS
    )
    juju.deploy(
        charm=DB_APP_NAME,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        channel="16/stable",
        revision=revision,
        config={"profile": "testing"},
        num_units=NUM_UNITS,
    )
    juju.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_NAME,
        base="ubuntu@24.04",
        channel="latest/edge",
        num_units=1,
    )
    juju.integrate(f"{DB_APP_NAME}:database", f"{DB_TEST_APP_NAME}:database")

    logger.info("Waiting for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, DB_TEST_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_block_versioned_dir_and_upgrade(juju: Juju, charm: str) -> None:
    """Block the versioned target directory and attempt upgrade.

    The snap's post-refresh hook runs migrate-data.sh which tries to
    mv data into 16/main/.  With the parent directory unwritable, the
    mv fails and the snap hook errors out (set -e), causing snapd to
    revert the snap.  The force-refresh-start action fails with a
    SnapError (action failure, not hook crash).
    """
    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    for unit in unit_names:
        logger.info("Creating unwritable versioned dir on %s to block migration", unit)
        juju.ssh(unit, f"sudo mkdir -p {DATA_ROOT}/16")
        juju.ssh(unit, f"sudo chmod 000 {DATA_ROOT}/16")

    leader = get_app_leader(juju, DB_APP_NAME)
    juju.run(unit=leader, action="pre-refresh-check")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    logger.info("Refreshing to local charm (expecting post-refresh hook failure)")
    juju.refresh(app=DB_APP_NAME, path=charm)

    action_failed = False
    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            logger.info("Forcing refresh start despite incompatibility")
            try:
                juju.run(
                    unit=unit_names[-1],
                    action="force-refresh-start",
                    params={"check-compatibility": False},
                    wait=10 * MINUTE_SECS,
                )
            except TaskError:
                action_failed = True
                logger.info("force-refresh-start failed as expected (snap hook failure)")
    except TimeoutError:
        pass

    if action_failed:
        logger.info("Action failed — snap was reverted by snapd, unit not in error state")
        for unit in unit_names:
            result = juju.ssh(unit, "snap info charmed-postgresql 2>/dev/null | grep installed")
            logger.info("Snap version on %s: %s", unit, result.strip())
    else:
        logger.info("Waiting for unit(s) to enter error state due to snap hook failure")
        juju.wait(
            lambda status: any(
                u.workload_status.current == "error"
                for u in status.apps[DB_APP_NAME].units.values()
            ),
            timeout=10 * MINUTE_SECS,
        )
        logger.info("Unit(s) in error state as expected — snap migration was blocked")


def test_restore_permissions_and_recover_upgrade(juju: Juju) -> None:
    """Restore directory permissions and retry upgrade so it completes."""
    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    for unit in unit_names:
        logger.info("Removing blocked directory and restoring on %s", unit)
        juju.ssh(unit, f"sudo rm -rf {DATA_ROOT}/16")

    _wait_for_refresh_recovery(
        juju, unit_names, "Cluster did not recover after restoring permissions"
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )

    leader = get_app_leader(juju, DB_APP_NAME)
    _assert_path_exists(juju, leader, f"{DATA_VERSIONED}/PG_VERSION")
    logger.info("Upgrade recovered — versioned layout verified")


def test_verify_data_after_recovery(juju: Juju) -> None:
    """Verify the database is functional after recovering from migration failure."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE recovery_check (id serial PRIMARY KEY, data text);",
            "INSERT INTO recovery_check (data) VALUES ('post_migration_failure');",
            "SELECT data FROM recovery_check WHERE data = 'post_migration_failure';",
            "DROP TABLE recovery_check;",
            "SELECT 1;",
        ],
        "postgres",
    )
    logger.info("Database functional after migration failure recovery")


# ---- Phase 2: Block rollback migration, recover, rollback ----


def test_block_root_dir_and_rollback(juju: Juju) -> None:
    """Block the root directory and attempt rollback.

    The snap's pre-refresh hook runs migrate-data.sh which tries to
    mv data from 16/main/ back to root.  A directory named PG_VERSION
    at root blocks the mv (cannot overwrite directory with file) and
    the hook errors (set -e), aborting the snap refresh entirely.
    """
    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    for unit in unit_names:
        logger.info("Creating blocking PG_VERSION directory on %s", unit)
        juju.ssh(unit, f"sudo mkdir -p {DATA_ROOT}/PG_VERSION")
        juju.ssh(unit, f"sudo chown _daemon_:_daemon_ {DATA_ROOT}/PG_VERSION")

    logger.info("Attempting rollback to 16/stable (expecting pre-refresh hook failure)")
    juju.cli("refresh", DB_APP_NAME, "--switch", f"ch:{DB_APP_NAME}", "--channel", "16/stable")

    action_failed = False
    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            logger.info("Forcing refresh start for rollback despite incompatibility")
            try:
                juju.run(
                    unit=unit_names[-1],
                    action="force-refresh-start",
                    params={"check-compatibility": False},
                    wait=10 * MINUTE_SECS,
                )
            except TaskError:
                action_failed = True
                logger.info(
                    "force-refresh-start failed as expected (snap hook failure on rollback)"
                )
    except TimeoutError:
        pass

    if action_failed:
        logger.info("Action failed — snap pre-refresh hook blocked, snap not reverted")
        for unit in unit_names:
            result = juju.ssh(unit, "snap info charmed-postgresql 2>/dev/null | grep installed")
            logger.info("Snap version on %s: %s", unit, result.strip())
    else:
        logger.info("Waiting for unit(s) to enter error state due to snap hook failure")
        juju.wait(
            lambda status: any(
                u.workload_status.current == "error"
                for u in status.apps[DB_APP_NAME].units.values()
            ),
            timeout=10 * MINUTE_SECS,
        )
        logger.info("Unit(s) in error state as expected — snap rollback migration was blocked")


def test_restore_permissions_and_recover_rollback(juju: Juju) -> None:
    """Restore directory permissions and resolve units so rollback completes."""
    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    for unit in unit_names:
        logger.info("Removing blocking PG_VERSION directory on %s", unit)
        juju.ssh(unit, f"sudo rmdir {DATA_ROOT}/PG_VERSION")

    _wait_for_refresh_recovery(
        juju, unit_names, "Cluster did not recover after restoring permissions for rollback"
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    leader = get_app_leader(juju, DB_APP_NAME)
    _assert_path_exists(juju, leader, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_missing(juju, leader, f"{DATA_VERSIONED}/PG_VERSION")
    logger.info("Rollback recovered — root layout verified")


def test_verify_data_after_rollback_recovery(juju: Juju) -> None:
    """Verify the database is functional after recovering from rollback failure."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE rollback_recovery_check (id serial PRIMARY KEY);",
            "DROP TABLE rollback_recovery_check;",
            "SELECT 1;",
        ],
        "postgres",
    )
    logger.info("Database functional after rollback failure recovery")
