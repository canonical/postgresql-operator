# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: temp tablespace object detection during upgrade/rollback.

Verifies that objects in the temp tablespace are detected and handled:

Phase 1 — Objects created before the initial upgrade from 16/stable.  The
stable charm has no temp-object check, so the upgrade proceeds past
pre-refresh-check.  However, force-refresh-start runs the new charm's
checks and detects the objects.  After clearing them, the upgrade completes.

Phase 2 — Objects created after the upgrade (versioned layout).  The
pre-refresh-check action detects them and blocks until they are cleared.

Phase 3 — Same blocking behaviour verified before a rollback.

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


def _get_temp_tablespace_location(juju: Juju, unit: str) -> str:
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["SELECT pg_tablespace_location(oid) FROM pg_tablespace WHERE spcname = 'temp';"],
        "postgres",
    )
    return result[0] if result else ""


def _complete_rolling_refresh(juju: Juju) -> None:
    """Drive a multi-unit rolling refresh to completion.

    After force-refresh-start upgrades the first unit, this polls the app
    status for resume-refresh prompts and runs them for each remaining unit.
    """
    deadline = time.time() + 20 * MINUTE_SECS
    while time.time() < deadline:
        status = juju.status()
        if status.apps[DB_APP_NAME].is_active:
            return

        app_msg = status.apps[DB_APP_NAME].app_status.message or ""
        if "resume-refresh" in app_msg:
            match = re.search(r"on unit (\d+)", app_msg)
            if match:
                resume_unit = f"{DB_APP_NAME}/{match.group(1)}"
                logger.info("Running resume-refresh on %s", resume_unit)
                try:
                    juju.run(unit=resume_unit, action="resume-refresh", wait=15 * MINUTE_SECS)
                except TaskError:
                    logger.info("resume-refresh failed on %s, will retry", resume_unit)
                continue

        time.sleep(30)

    raise TimeoutError("Rolling refresh did not complete within deadline")


def _get_temp_object_count(juju: Juju, unit: str) -> int:
    """Return the number of persistent objects in the temp tablespace."""
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "SELECT count(*) FROM pg_class WHERE reltablespace = "
            "(SELECT oid FROM pg_tablespace WHERE spcname = 'temp');"
        ],
        "postgres",
    )
    return int(result[0]) if result else 0


# ---- Phase 1: Deploy stable, create objects, upgrade to local ----


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


def test_create_objects_before_initial_upgrade(juju: Juju) -> None:
    """Create persistent objects in the temp tablespace before upgrading from stable."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Creating a persistent table in the temp tablespace on %s (stable charm)", unit)
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE pre_upgrade_blocker (id serial PRIMARY KEY, data text) TABLESPACE temp;",
            "INSERT INTO pre_upgrade_blocker (data) VALUES ('should_block_migration');",
            "SELECT 1;",
        ],
        "postgres",
    )

    count = _get_temp_object_count(juju, unit)
    assert count >= 1, f"Expected at least 1 object in temp tablespace, got {count}"
    logger.info("Created %d object(s) in temp tablespace before upgrade", count)


def test_initial_upgrade_blocked_by_objects(juju: Juju, charm: str) -> None:
    """Upgrade to local charm — force-refresh-start detects temp objects and blocks."""
    leader = get_app_leader(juju, DB_APP_NAME)
    juju.run(unit=leader, action="pre-refresh-check")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    logger.info("Refreshing to local charm (expecting block due to temp objects)")
    juju.refresh(app=DB_APP_NAME, path=charm)

    juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
        logger.info("Running force-refresh-start — expecting failure due to temp objects")
        try:
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=10 * MINUTE_SECS,
            )
            raise AssertionError(
                "Expected force-refresh-start to fail due to temp objects but it succeeded"
            )
        except TaskError as e:
            logger.info("force-refresh-start correctly blocked: %s", e)

    logger.info("Temp objects blocked the upgrade as expected")


def test_clear_objects_and_complete_upgrade(juju: Juju) -> None:
    """Drop temp objects and re-run force-refresh-start so upgrade completes."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Dropping pre_upgrade_blocker table on %s", unit)
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE pre_upgrade_blocker;", "SELECT 1;"],
        "postgres",
    )

    count = _get_temp_object_count(juju, unit)
    assert count == 0, f"Expected 0 objects in temp tablespace after cleanup, got {count}"

    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    logger.info("Re-running force-refresh-start after clearing objects")
    try:
        juju.run(
            unit=unit_names[-1],
            action="force-refresh-start",
            params={"check-compatibility": False},
            wait=15 * MINUTE_SECS,
        )
    except TimeoutError:
        logger.info("force-refresh-start timed out, continuing with rolling refresh")

    _complete_rolling_refresh(juju)

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")
    logger.info("Upgrade completed after clearing temp objects — versioned layout verified")


# ---- Phase 2: Create objects and verify blocking before re-upgrade ----


def test_create_temp_tablespace_objects(juju: Juju) -> None:
    """Create a persistent table in the temp tablespace to simulate objects blocking refresh."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Creating a persistent table in the temp tablespace on %s", unit)
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE temp_resident (id serial PRIMARY KEY, data text) TABLESPACE temp;",
            "INSERT INTO temp_resident (data) VALUES ('blocking_object');",
            "SELECT 1;",
        ],
        "postgres",
    )

    count = _get_temp_object_count(juju, unit)
    assert count >= 1, f"Expected at least 1 object in temp tablespace, got {count}"
    logger.info("Created %d object(s) in temp tablespace", count)


def test_pre_refresh_check_blocks_with_objects(juju: Juju) -> None:
    """Verify that pre-refresh-check fails when objects exist in the temp tablespace."""
    leader = get_app_leader(juju, DB_APP_NAME)

    logger.info("Running pre-refresh-check — expecting it to fail due to temp objects")
    try:
        juju.run(unit=leader, action="pre-refresh-check")
        raise AssertionError("Expected pre-refresh-check to fail but it succeeded")
    except TaskError as e:
        logger.info("pre-refresh-check correctly blocked: %s", e)


def test_clear_temp_objects_and_verify(juju: Juju) -> None:
    """Drop the blocking objects and verify the temp tablespace is clean."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Dropping temp_resident table")
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE temp_resident;", "SELECT 1;"],
        "postgres",
    )

    count = _get_temp_object_count(juju, unit)
    assert count == 0, f"Expected 0 objects in temp tablespace after cleanup, got {count}"
    logger.info("Temp tablespace clean — %d objects", count)


def test_pre_refresh_check_passes_after_cleanup(juju: Juju) -> None:
    """Verify that pre-refresh-check succeeds after clearing temp objects."""
    leader = get_app_leader(juju, DB_APP_NAME)

    logger.info("Running pre-refresh-check — expecting success")
    result = juju.run(unit=leader, action="pre-refresh-check")
    assert result.status == "completed", (
        f"Expected pre-refresh-check to pass but got status={result.status}: {result.results}"
    )
    logger.info("pre-refresh-check passed after cleanup")


# ---- Phase 3: Create objects before rollback and verify blocking ----


def test_create_temp_objects_before_rollback(juju: Juju) -> None:
    """Create objects in temp tablespace again to test rollback blocking."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Creating table in temp tablespace before rollback on %s", unit)
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE rollback_blocker (id serial PRIMARY KEY, data text) TABLESPACE temp;",
            "INSERT INTO rollback_blocker (data) VALUES ('blocking_rollback');",
            "SELECT 1;",
        ],
        "postgres",
    )

    count = _get_temp_object_count(juju, unit)
    assert count >= 1, f"Expected at least 1 object in temp tablespace, got {count}"
    logger.info("Created %d object(s) in temp tablespace before rollback", count)


def test_pre_refresh_check_blocks_rollback(juju: Juju) -> None:
    """Verify that pre-refresh-check fails before rollback when objects exist."""
    leader = get_app_leader(juju, DB_APP_NAME)

    logger.info("Running pre-refresh-check before rollback — expecting failure")
    try:
        juju.run(unit=leader, action="pre-refresh-check")
        raise AssertionError("Expected pre-refresh-check to fail but it succeeded")
    except TaskError as e:
        logger.info("pre-refresh-check correctly blocked rollback: %s", e)


def test_clear_objects_and_rollback(juju: Juju) -> None:
    """Clear temp objects and roll back to stable."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Dropping rollback_blocker table")
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE rollback_blocker;", "SELECT 1;"],
        "postgres",
    )

    count = _get_temp_object_count(juju, unit)
    assert count == 0, f"Expected 0 objects after cleanup, got {count}"

    logger.info("Rolling back to 16/stable")
    juju.cli("refresh", DB_APP_NAME, "--switch", f"ch:{DB_APP_NAME}", "--channel", "16/stable")

    logger.info("Waiting for rollback to block")
    juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())

    if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
        logger.info("Rollback blocked due to incompatibility, forcing start")
        try:
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=15 * MINUTE_SECS,
            )
        except TimeoutError:
            logger.info("force-refresh-start timed out, continuing with rolling refresh")

    _complete_rolling_refresh(juju)

    logger.info("Waiting for rollback to finish")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_verify_root_layout_after_rollback(juju: Juju) -> None:
    """Verify root layout and temp tablespace after rollback."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    logger.info("Verifying root layout after rollback on %s", unit)

    _assert_path_exists(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/base")
    _assert_path_missing(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")

    location = _get_temp_tablespace_location(juju, unit)
    assert location == TEMP_ROOT, (
        f"Expected temp tablespace at {TEMP_ROOT} after rollback, got {location}"
    )

    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE post_rollback_check (id serial PRIMARY KEY);",
            "DROP TABLE post_rollback_check;",
            "SELECT 1;",
        ],
        "postgres",
    )
    logger.info("Root layout verified and database functional after rollback")
