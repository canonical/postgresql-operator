# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: idempotency of versioned storage migration.

Verifies that re-upgrading a cluster already on versioned layout and
re-rolling-back a cluster already on root layout are both no-ops.

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


def _do_upgrade(juju: Juju, charm: str) -> None:
    """Upgrade from current charm to the local build."""
    leader = get_app_leader(juju, DB_APP_NAME)
    juju.run(unit=leader, action="pre-refresh-check")
    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    juju.refresh(app=DB_APP_NAME, path=charm)

    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

        units = get_app_units(juju, DB_APP_NAME)
        unit_names = sorted(units.keys())

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=10 * MINUTE_SECS,
            )

        juju.wait(jubilant.all_agents_idle, timeout=10 * MINUTE_SECS)

        if not juju.status().apps[DB_APP_NAME].is_active:
            juju.run(unit=unit_names[1], action="resume-refresh", wait=15 * MINUTE_SECS)
    except TimeoutError:
        assert juju.status().apps[DB_APP_NAME].is_active

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def _do_rollback(juju: Juju) -> None:
    """Roll back to the stable channel."""
    juju.cli("refresh", DB_APP_NAME, "--switch", f"ch:{DB_APP_NAME}", "--channel", "16/stable")

    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)

        units = get_app_units(juju, DB_APP_NAME)
        unit_names = sorted(units.keys())

        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=10 * MINUTE_SECS,
            )

        juju.wait(jubilant.all_agents_idle, timeout=10 * MINUTE_SECS)

        if not juju.status().apps[DB_APP_NAME].is_active:
            juju.run(unit=unit_names[1], action="resume-refresh", wait=15 * MINUTE_SECS)
    except TimeoutError:
        assert juju.status().apps[DB_APP_NAME].is_active

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def _verify_versioned_layout(juju: Juju, unit: str) -> None:
    """Assert the cluster uses versioned storage layout."""
    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")
    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/base")
    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/global")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/base")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/global")

    location = _get_temp_tablespace_location(juju, unit)
    assert location == TEMP_VERSIONED, (
        f"Expected temp tablespace at {TEMP_VERSIONED}, got {location}"
    )


def _verify_root_layout(juju: Juju, unit: str) -> None:
    """Assert the cluster uses root storage layout."""
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/base")
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/global")
    _assert_path_missing(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")

    location = _get_temp_tablespace_location(juju, unit)
    assert location == TEMP_ROOT, f"Expected temp tablespace at {TEMP_ROOT}, got {location}"


# ---- Phase 1: Deploy stable + initial upgrade ----


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


def test_initial_upgrade(juju: Juju, charm: str) -> None:
    """Upgrade from stable to local to reach the versioned layout."""
    logger.info("Performing initial upgrade to versioned layout")
    _do_upgrade(juju, charm)

    unit = get_app_leader(juju, DB_APP_NAME)
    _verify_versioned_layout(juju, unit)
    logger.info("Initial upgrade verified — cluster is on versioned layout")


# ---- Phase 2: Re-upgrade (already versioned) ----


def test_re_upgrade_is_noop(juju: Juju, charm: str) -> None:
    """Upgrade again when already on versioned layout — should be a no-op."""
    unit = get_app_leader(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE idempotency_marker (id serial PRIMARY KEY, value text);",
            "INSERT INTO idempotency_marker (value) VALUES ('before_re_upgrade');",
            "SELECT 1;",
        ],
        "postgres",
    )

    logger.info("Re-upgrading (already on versioned layout)")
    _do_upgrade(juju, charm)

    unit = get_app_leader(juju, DB_APP_NAME)
    _verify_versioned_layout(juju, unit)

    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["SELECT value FROM idempotency_marker ORDER BY id;"],
        "postgres",
    )
    assert result == ["before_re_upgrade"], f"Expected ['before_re_upgrade'], got {result}"

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE idempotency_marker;", "SELECT 1;"],
        "postgres",
    )
    logger.info("Re-upgrade verified as no-op — layout and data intact")


# ---- Phase 3: Rollback to root ----


def test_rollback_to_root(juju: Juju) -> None:
    """Roll back to stable to reach root layout."""
    logger.info("Rolling back to root layout")
    _do_rollback(juju)

    unit = get_app_leader(juju, DB_APP_NAME)
    _verify_root_layout(juju, unit)
    logger.info("Rollback verified — cluster is on root layout")


# ---- Phase 4: Re-rollback (already root) ----


def test_re_rollback_is_noop(juju: Juju) -> None:
    """Roll back again when already on root layout — should be a no-op."""
    unit = get_db_primary_unit(juju, DB_APP_NAME)
    password = _get_operator_password(juju)
    ip = get_unit_ip(juju, DB_APP_NAME, unit)

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        [
            "CREATE TABLE rollback_marker (id serial PRIMARY KEY, value text);",
            "INSERT INTO rollback_marker (value) VALUES ('before_re_rollback');",
            "SELECT 1;",
        ],
        "postgres",
    )

    logger.info("Re-rolling back (already on root layout)")
    _do_rollback(juju)

    unit = get_app_leader(juju, DB_APP_NAME)
    _verify_root_layout(juju, unit)

    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    result = execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["SELECT value FROM rollback_marker ORDER BY id;"],
        "postgres",
    )
    assert result == ["before_re_rollback"], f"Expected ['before_re_rollback'], got {result}"

    execute_queries_on_unit(
        ip,
        "operator",
        password,
        ["DROP TABLE rollback_marker;", "SELECT 1;"],
        "postgres",
    )
    logger.info("Re-rollback verified as no-op — layout and data intact")
