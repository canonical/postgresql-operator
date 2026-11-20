# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: backup before the versioned-storage refactoring, restore after it.

Verifies https://github.com/canonical/postgresql-operator/issues/1873: a backup
created on a charm revision that predates the storage refactoring (data at the
storage mount root) can still be listed and restored after the charm is
refreshed to the current versioned storage layout (data under ``16/main``).

Uses a microceph TLS RGW as the S3 backend, like ``test_backups_ceph.py``.
"""

import logging
import platform

import jubilant
import pytest
from jubilant import Juju
from single_kernel_postgresql.config import literals

from .conftest import ConnectionInformation
from .helpers import execute_queries_on_unit
from .high_availability.high_availability_helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_unit_ip,
    get_user_password,
    wait_for_apps_status,
)

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DB_APP_NAME = "postgresql"
S3_INTEGRATOR_APP_NAME = "s3-integrator"

# The last 16/stable charm revisions before the storage refactoring introduced
# the versioned layout (charm-side counterpart: PR #1649), per architecture.
STABLE_REVISIONS = {"amd64": 952, "arm64": 951}

BACKUP_TABLE = "backup_table_1"
POST_REFRESH_TABLE = "backup_table_2"

DATA_ROOT = f"{literals.BASE_SNAP_DIR}/{literals.SNAP_COMMON}/{literals.VM_DATA_PATH}"
DATA_VERSIONED = f"{DATA_ROOT}/16/main"


def _assert_path_exists(juju: Juju, unit: str, path: str) -> None:
    result = juju.ssh(unit, f"sudo test -e {path} && echo exists || echo missing")
    assert "exists" in result, f"Expected {path} to exist on {unit}, but it is missing"


def _assert_path_missing(juju: Juju, unit: str, path: str) -> None:
    result = juju.ssh(unit, f"sudo test -e {path} && echo exists || echo missing")
    assert "missing" in result, f"Expected {path} to be absent on {unit}, but it exists"


def _run_sql(juju: Juju, queries: list[str]) -> list:
    unit = get_app_leader(juju, DB_APP_NAME)
    password = get_user_password(juju, DB_APP_NAME, "operator")
    ip = get_unit_ip(juju, DB_APP_NAME, unit)
    return execute_queries_on_unit(ip, "operator", password, queries, "postgres")


def _get_most_recent_backup(juju: Juju) -> str:
    unit = get_app_leader(juju, DB_APP_NAME)
    listing = juju.run(unit=unit, action="list-backups")
    backups = listing.results.get("backups")
    assert backups, "backups not outputted"
    return backups.split("\n")[-1].split()[0]


@pytest.fixture(scope="session")
def cloud_credentials(microceph: ConnectionInformation) -> dict[str, str]:
    """Read the microceph RGW credentials."""
    return {
        "access-key": microceph.access_key_id,
        "secret-key": microceph.secret_access_key,
    }


@pytest.fixture(scope="session")
def cloud_configs(microceph: ConnectionInformation):
    """Read the microceph TLS RGW connection information."""
    return {
        "endpoint": f"https://{microceph.host}",
        "bucket": microceph.bucket,
        "path": "/pg",
        "region": "",
        "s3-uri-style": "path",
        "tls-ca-chain": microceph.cert,
    }


def test_deploy_stable(juju: Juju, cloud_configs, cloud_credentials) -> None:
    """Deploy PostgreSQL from the pre-refactoring 16/stable revision and configure S3 backups."""
    arch = platform.machine()
    if arch == "x86_64":
        arch = "amd64"
    elif arch == "aarch64":
        arch = "arm64"
    revision = STABLE_REVISIONS[arch]

    logger.info("Deploying PostgreSQL from 16/stable revision %d", revision)
    juju.deploy(
        charm=DB_APP_NAME,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        channel="16/stable",
        revision=revision,
        config={"profile": "testing"},
        num_units=1,
    )
    juju.deploy(charm=S3_INTEGRATOR_APP_NAME, app=S3_INTEGRATOR_APP_NAME, num_units=1)

    # Wait for the units' agents to settle before running actions: actions on
    # a still-installing unit fail with "no actions defined on charm". The app
    # itself only reaches active AFTER the credentials are configured (it
    # waits for access-key/secret-key), so waiting for active here would time
    # out.
    logger.info("Waiting for the agents to settle")
    juju.wait(jubilant.all_agents_idle, timeout=10 * MINUTE_SECS)

    logger.info("Configuring the S3 integrator")
    juju.config(S3_INTEGRATOR_APP_NAME, cloud_configs)
    juju.run(
        unit=f"{S3_INTEGRATOR_APP_NAME}/0", action="sync-s3-credentials", params=cloud_credentials
    )
    juju.integrate(f"{DB_APP_NAME}:s3-parameters", f"{S3_INTEGRATOR_APP_NAME}:s3-parameters")

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, S3_INTEGRATOR_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_verify_root_layout(juju: Juju) -> None:
    """Verify that the pre-refactoring charm stores the data at the storage mount root."""
    unit = get_app_leader(juju, DB_APP_NAME)
    result = _run_sql(juju, ["SHOW data_directory;"])
    assert result[0] == DATA_ROOT, f"Expected data_directory={DATA_ROOT}, got {result[0]}"
    _assert_path_exists(juju, unit, f"{DATA_ROOT}/PG_VERSION")
    _assert_path_missing(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")


def test_create_backup(juju: Juju) -> None:
    """Create test data and take a backup on the pre-refactoring charm."""
    _run_sql(
        juju,
        [
            f"CREATE TABLE {BACKUP_TABLE} (id serial PRIMARY KEY, value text);",
            f"INSERT INTO {BACKUP_TABLE} (value) VALUES ('before_refresh');",
            f"SELECT count(*) FROM {BACKUP_TABLE};",
        ],
    )

    unit = get_app_leader(juju, DB_APP_NAME)
    logger.info("Creating a backup on %s", unit)
    action = juju.run(unit=unit, action="create-backup")
    assert action.results.get("backup-status"), "backup hasn't succeeded"
    logger.info("Created backup %s", _get_most_recent_backup(juju))


def test_refresh_to_local_charm(juju: Juju, charm: str) -> None:
    """Refresh to the current charm built from the repo and verify the versioned layout."""
    logger.info("Refreshing the charm to the local build: %s", charm)
    juju.refresh(app=DB_APP_NAME, path=charm)

    try:
        juju.wait(lambda status: status.apps[DB_APP_NAME].is_blocked, timeout=10 * MINUTE_SECS)
        if "Refresh incompatible" in juju.status().apps[DB_APP_NAME].app_status.message:
            logger.info("Refresh blocked due to incompatibility, forcing start")
            juju.run(
                unit=get_app_leader(juju, DB_APP_NAME),
                action="force-refresh-start",
                params={"check-compatibility": False},
                wait=40 * MINUTE_SECS,
            )
        juju.wait(jubilant.all_agents_idle, timeout=10 * MINUTE_SECS)
    except TimeoutError:
        logger.info("Upgrade completed without blocking")

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, S3_INTEGRATOR_APP_NAME),
        timeout=40 * MINUTE_SECS,
    )

    unit = get_app_leader(juju, DB_APP_NAME)
    result = _run_sql(juju, ["SHOW data_directory;"])
    assert result[0] == DATA_VERSIONED, (
        f"Expected data_directory={DATA_VERSIONED} after refresh, got {result[0]}"
    )
    _assert_path_exists(juju, unit, f"{DATA_VERSIONED}/PG_VERSION")
    _assert_path_missing(juju, unit, f"{DATA_ROOT}/PG_VERSION")

    result = _run_sql(
        juju,
        [
            f"SELECT value FROM {BACKUP_TABLE} ORDER BY id;",
            f"CREATE TABLE {POST_REFRESH_TABLE} (id serial PRIMARY KEY, value text);",
            f"INSERT INTO {POST_REFRESH_TABLE} (value) VALUES ('after_refresh');",
            "SELECT 1;",
        ],
    )
    assert result[0] == ["before_refresh"], f"Expected ['before_refresh'], got {result[0]}"


def test_backup_still_listed(juju: Juju) -> None:
    """Verify the pre-refresh backup is still listed after the storage layout migration."""
    backup_id = _get_most_recent_backup(juju)
    logger.info("Backup %s still listed after the refresh", backup_id)


def test_restore_backup(juju: Juju) -> None:
    """Restore the pre-refactoring backup on the versioned-layout cluster."""
    unit = get_app_leader(juju, DB_APP_NAME)
    backup_id = _get_most_recent_backup(juju)
    logger.info("Restoring backup %s on %s", backup_id, unit)
    action = juju.run(
        unit=unit,
        action="restore",
        params={"backup-id": backup_id},
        wait=40 * MINUTE_SECS,
    )
    assert action.results.get("restore-status"), "restore hasn't succeeded"

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, S3_INTEGRATOR_APP_NAME),
        timeout=40 * MINUTE_SECS,
    )

    result = _run_sql(juju, ["SHOW data_directory;"])
    assert result[0] == DATA_VERSIONED, (
        f"Expected data_directory={DATA_VERSIONED} after restore, got {result[0]}"
    )
    result = _run_sql(juju, [f"SELECT value FROM {BACKUP_TABLE} ORDER BY id;", "SELECT 1;"])
    assert result[0] == ["before_refresh"], f"Expected ['before_refresh'], got {result[0]}"
    result = _run_sql(juju, [f"SELECT to_regclass('{POST_REFRESH_TABLE}') IS NULL;"])
    assert result[0] is True, f"{POST_REFRESH_TABLE} should be gone after the restore"

    _run_sql(
        juju,
        [
            "CREATE TABLE restore_check (id serial PRIMARY KEY, value text);",
            "INSERT INTO restore_check (value) VALUES ('after_restore');",
            "SELECT 1;",
        ],
    )
    logger.info("Backup restored successfully on the versioned storage layout")
