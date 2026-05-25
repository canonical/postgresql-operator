# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: upgrade and rollback with versioned storage layout.

Deploys from pinned 16/stable revisions, creates test data,
upgrades to the local charm (which uses versioned 16/main subdirectories),
verifies forward migration, then rolls back and verifies reverse migration.

The number of PostgreSQL units is controlled by the NUM_UNITS environment
variable (default: 1) for spread parametrization.
"""

import logging
import os
import platform

import jubilant
from jubilant import Juju

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
ARCHIVE_ROOT = f"{SNAP_COMMON}/data/archive"
LOGS_ROOT = f"{SNAP_COMMON}/data/logs"

VERSIONED_SUFFIX = "16/main"
DATA_VERSIONED = f"{DATA_ROOT}/{VERSIONED_SUFFIX}"
TEMP_VERSIONED = f"{TEMP_ROOT}/{VERSIONED_SUFFIX}"
ARCHIVE_VERSIONED = f"{ARCHIVE_ROOT}/{VERSIONED_SUFFIX}"
LOGS_VERSIONED = f"{LOGS_ROOT}/{VERSIONED_SUFFIX}"


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


def test_verify_root_layout(juju: Juju) -> None:
    """Verify that the stable revision uses the root (non-versioned) storage layout."""
    unit = get_app_leader(juju, DB_APP_NAME)
    logger.info("Verifying root storage layout on %s", unit)

    _assert_path_exists(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/base")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/global")
    _assert_path_missing(juju, unit, DATA_VERSIONED)

    location = _get_temp_tablespace_location(juju, unit)
    assert location == TEMP_ROOT, f"Expected temp tablespace at {TEMP_ROOT}, got {location}"

    result = juju.ssh(unit, f"sudo readlink {DATA_ROOT}/pg_wal")
    assert LOGS_ROOT in result, f"Expected pg_wal symlink to point to {LOGS_ROOT}, got {result}"

    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip, "operator", password, ["SHOW data_directory;"], "postgres"
    )
    assert result[0] == DATA_ROOT, (
        f"Expected PostgreSQL data_directory={DATA_ROOT}, got {result[0]}"
    )


def test_create_test_data(juju: Juju) -> None:
    """Create test data to verify it survives the upgrade."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    logger.info("Creating test table on %s", unit)
    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE upgrade_test_data (id serial PRIMARY KEY, value text);",
            "INSERT INTO upgrade_test_data (value) VALUES ('before_upgrade');",
            "SELECT count(*) FROM upgrade_test_data;",
        ],
        "postgres",
    )
    assert result[0] == 1, f"Expected 1 row in upgrade_test_data, got {result[0]}"
    logger.info("Test table created with 1 row")


def test_upgrade_to_local(juju: Juju, charm: str) -> None:
    """Upgrade from stable to the local charm and verify versioned storage layout."""
    logger.info("Running pre-refresh-check action")
    leader = get_app_leader(juju, DB_APP_NAME)
    juju.run(unit=leader, action="pre-refresh-check")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    logger.info("Refreshing charm to local build: %s", charm)
    juju.refresh(app=DB_APP_NAME, path=charm)

    logger.info("Waiting for refresh to complete or block")
    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

        units = get_app_units(juju, DB_APP_NAME)
        unit_names = sorted(units.keys())

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            logger.info("Refresh blocked due to incompatibility, forcing start")
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=10 * MINUTE_SECS,
            )

        juju.wait(jubilant.all_agents_idle, timeout=10 * MINUTE_SECS)

        if not juju.status().apps[DB_APP_NAME].is_active:
            logger.info("Running resume-refresh action")
            juju.run(unit=unit_names[1], action="resume-refresh", wait=15 * MINUTE_SECS)
    except TimeoutError:
        logger.info("Upgrade completed without blocking (charm-only upgrade)")
        assert juju.status().apps[DB_APP_NAME].is_active

    logger.info("Waiting for upgrade to finish")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_verify_versioned_layout(juju: Juju) -> None:
    """Verify that after upgrade the data lives under 16/main subdirectories."""
    unit = get_app_leader(juju, DB_APP_NAME)
    logger.info("Verifying versioned storage layout on %s", unit)

    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")
    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/base")
    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/global")

    _assert_path_missing(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/base")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/global")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/postgresql.conf")

    location = _get_temp_tablespace_location(juju, unit)
    assert location == TEMP_VERSIONED, (
        f"Expected temp tablespace at {TEMP_VERSIONED}, got {location}"
    )

    result = juju.ssh(unit, f"sudo readlink {DATA_VERSIONED}/pg_wal")
    assert LOGS_VERSIONED in result, (
        f"Expected pg_wal symlink to point to {LOGS_VERSIONED}, got {result}"
    )

    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip, "operator", password, ["SHOW data_directory;"], "postgres"
    )
    assert result[0] == DATA_VERSIONED, (
        f"Expected PostgreSQL data_directory={DATA_VERSIONED}, got {result[0]}"
    )


def test_verify_data_after_upgrade(juju: Juju) -> None:
    """Verify that user data survived the upgrade."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["SELECT value FROM upgrade_test_data ORDER BY id;"],
        "postgres",
    )
    assert result == ["before_upgrade"], f"Expected ['before_upgrade'], got {result}"

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["INSERT INTO upgrade_test_data (value) VALUES ('after_upgrade');", "SELECT 1;"],
        "postgres",
    )
    logger.info("Data integrity verified after upgrade")


def test_rollback_to_stable(juju: Juju) -> None:
    """Roll back to the stable revision and verify reverse migration."""
    logger.info("Dropping test table before rollback")
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE IF EXISTS upgrade_test_data;", "SELECT 1;"],
        "postgres",
    )

    logger.info("Rolling back to 16/stable")
    juju.cli("refresh", DB_APP_NAME, "--switch", f"ch:{DB_APP_NAME}", "--channel", "16/stable")

    logger.info("Waiting for rollback to complete or block")
    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

        units = get_app_units(juju, DB_APP_NAME)
        unit_names = sorted(units.keys())

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            logger.info("Rollback blocked due to incompatibility, forcing start")
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=10 * MINUTE_SECS,
            )

        juju.wait(jubilant.all_agents_idle, timeout=10 * MINUTE_SECS)

        if not juju.status().apps[DB_APP_NAME].is_active:
            logger.info("Running resume-refresh action")
            juju.run(unit=unit_names[1], action="resume-refresh", wait=15 * MINUTE_SECS)
    except TimeoutError:
        logger.info("Rollback completed without blocking")
        assert juju.status().apps[DB_APP_NAME].is_active

    logger.info("Waiting for rollback to finish")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_verify_root_layout_after_rollback(juju: Juju) -> None:
    """Verify that after rollback the data is back at root paths."""
    unit = get_app_leader(juju, DB_APP_NAME)
    logger.info("Verifying root storage layout after rollback on %s", unit)

    _assert_path_exists(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/base")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/global")

    _assert_path_missing(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/16")
    _assert_path_missing(juju, unit, f"{ARCHIVE_ROOT}/16")
    _assert_path_missing(juju, unit, f"{LOGS_ROOT}/16")
    _assert_path_missing(juju, unit, f"{TEMP_ROOT}/16")

    location = _get_temp_tablespace_location(juju, unit)
    assert location == TEMP_ROOT, (
        f"Expected temp tablespace at {TEMP_ROOT} after rollback, got {location}"
    )

    result = juju.ssh(unit, f"sudo readlink {DATA_ROOT}/pg_wal")
    assert LOGS_ROOT in result, f"Expected pg_wal symlink to point to {LOGS_ROOT}, got {result}"

    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip, "operator", password, ["SHOW data_directory;"], "postgres"
    )
    assert result[0] == DATA_ROOT, (
        f"Expected PostgreSQL data_directory={DATA_ROOT}, got {result[0]}"
    )


def test_verify_data_after_rollback(juju: Juju) -> None:
    """Verify database is functional after rollback."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE rollback_test (id serial PRIMARY KEY, value text);",
            "INSERT INTO rollback_test (value) VALUES ('after_rollback');",
            "SELECT 1;",
        ],
        "postgres",
    )
    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["SELECT value FROM rollback_test;"],
        "postgres",
    )
    assert result == ["after_rollback"], f"Expected ['after_rollback'], got {result}"

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE rollback_test;", "SELECT 1;"],
        "postgres",
    )
    logger.info("Database functional after rollback")
