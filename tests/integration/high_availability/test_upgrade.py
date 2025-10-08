# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import platform
import shutil
import zipfile
from pathlib import Path

import jubilant_backports
import pytest
import tomli
import tomli_w
from jubilant_backports import Juju

from .high_availability_helpers_new import (
    check_db_units_writes_increment,
    get_app_leader,
    get_app_units,
    get_db_primary_unit,
    wait_for_apps_status,
)

DB_APP_NAME = "postgresql"
DB_TEST_APP_NAME = "postgresql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.mark.abort_on_fail
def test_deploy_latest(juju: Juju) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    logging.info("Deploying PostgreSQL cluster")
    juju.deploy(
        charm=DB_APP_NAME,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        channel="16/edge",
        config={"profile": "testing"},
        num_units=3,
    )
    juju.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        num_units=1,
    )

    juju.integrate(
        f"{DB_APP_NAME}:database",
        f"{DB_TEST_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, DB_APP_NAME, DB_TEST_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


@pytest.mark.abort_on_fail
async def test_pre_refresh_check(juju: Juju) -> None:
    """Test that the pre-refresh-check action runs successfully."""
    postgresql_leader = get_app_leader(juju, DB_APP_NAME)

    logging.info("Run pre-refresh-check action")
    task = juju.run(unit=postgresql_leader, action="pre-refresh-check")
    task.raise_on_failure()

    logging.info("Assert primary is set to leader")
    postgresql_primary = get_db_primary_unit(juju, DB_APP_NAME)
    assert postgresql_primary == postgresql_leader, "Primary unit not set to leader"


@pytest.mark.abort_on_fail
async def test_upgrade_from_edge(juju: Juju, charm: str, continuous_writes) -> None:
    """Update the second cluster."""
    logging.info("Ensure continuous writes are incrementing")
    await check_db_units_writes_increment(juju, DB_APP_NAME)

    logging.info("Refresh the charm")
    juju.refresh(app=DB_APP_NAME, path=charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant_backports.any_maintenance(status, DB_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=lambda status: jubilant_backports.all_active(status, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    await check_db_units_writes_increment(juju, DB_APP_NAME)


@pytest.mark.abort_on_fail
async def test_fail_and_rollback(juju: Juju, charm: str, continuous_writes) -> None:
    """Test an upgrade failure and its rollback."""
    db_app_leader = get_app_leader(juju, DB_APP_NAME)
    db_app_units = get_app_units(juju, DB_APP_NAME)

    logging.info("Run pre-refresh-check action")
    task = juju.run(unit=db_app_leader, action="pre-refresh-check")
    task.raise_on_failure()

    tmp_folder = Path("tmp")
    tmp_folder.mkdir(exist_ok=True)
    tmp_folder_charm = Path(tmp_folder, charm).absolute()

    shutil.copy(charm, tmp_folder_charm)

    logging.info("Inject dependency fault")
    inject_dependency_fault(juju, DB_APP_NAME, tmp_folder_charm)

    logging.info("Refresh the charm")
    juju.refresh(app=DB_APP_NAME, path=tmp_folder_charm)

    logging.info("Wait for upgrade to fail on leader")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, DB_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes on all units")
    await check_db_units_writes_increment(juju, DB_APP_NAME, list(db_app_units))

    logging.info("Re-run pre-refresh-check action")
    task = juju.run(unit=db_app_leader, action="pre-refresh-check")
    task.raise_on_failure()

    logging.info("Re-refresh the charm")
    juju.refresh(app=DB_APP_NAME, path=charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant_backports.any_maintenance(status, DB_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=lambda status: jubilant_backports.all_active(status, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes after rollback procedure")
    await check_db_units_writes_increment(juju, DB_APP_NAME, list(db_app_units))

    # Remove fault charm file
    tmp_folder_charm.unlink()


def inject_dependency_fault(juju: Juju, app_name: str, charm_file: str | Path) -> None:
    """Inject a dependency fault into the PostgreSQL charm."""
    with Path("refresh_versions.toml").open("rb") as file:
        versions = tomli.load(file)

    versions["charm"] = "16/0.0.0"
    versions["snap"]["revisions"][platform.machine()] = "1"

    # Overwrite refresh_versions.toml with incompatible version.
    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        charm_zip.writestr("refresh_versions.toml", tomli_w.dumps(versions))
