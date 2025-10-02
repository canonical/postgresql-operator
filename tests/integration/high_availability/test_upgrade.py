# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import shutil
import zipfile
from pathlib import Path

import jubilant_backports
import pytest
from jubilant_backports import Juju

from .high_availability_helpers_new import (
    check_mysql_units_writes_increment,
    get_app_leader,
    get_app_units,
    get_mysql_primary_unit,
    get_mysql_variable_value,
    get_relation_data,
    wait_for_apps_status,
)

MYSQL_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.mark.abort_on_fail
def test_deploy_latest(juju: Juju) -> None:
    """Simple test to ensure that the MySQL and application charms get deployed."""
    logging.info("Deploying MySQL cluster")
    juju.deploy(
        charm=MYSQL_APP_NAME,
        app=MYSQL_APP_NAME,
        base="ubuntu@22.04",
        channel="8.0/edge",
        config={"profile": "testing"},
        num_units=3,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        num_units=1,
    )

    juju.integrate(
        f"{MYSQL_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active, MYSQL_APP_NAME, MYSQL_TEST_APP_NAME
        ),
        error=jubilant_backports.any_blocked,
        timeout=20 * MINUTE_SECS,
    )


@pytest.mark.abort_on_fail
async def test_pre_upgrade_check(juju: Juju) -> None:
    """Test that the pre-upgrade-check action runs successfully."""
    mysql_leader = get_app_leader(juju, MYSQL_APP_NAME)
    mysql_units = get_app_units(juju, MYSQL_APP_NAME)

    logging.info("Run pre-upgrade-check action")
    task = juju.run(unit=mysql_leader, action="pre-upgrade-check")
    task.raise_on_failure()

    logging.info("Assert slow shutdown is enabled")
    for unit_name in mysql_units:
        value = await get_mysql_variable_value(
            juju, MYSQL_APP_NAME, unit_name, "innodb_fast_shutdown"
        )
        assert value == 0

    logging.info("Assert primary is set to leader")
    mysql_primary = get_mysql_primary_unit(juju, MYSQL_APP_NAME)
    assert mysql_primary == mysql_leader, "Primary unit not set to leader"


@pytest.mark.abort_on_fail
async def test_upgrade_from_edge(juju: Juju, charm: str, continuous_writes) -> None:
    """Update the second cluster."""
    logging.info("Ensure continuous writes are incrementing")
    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME)

    logging.info("Refresh the charm")
    juju.refresh(app=MYSQL_APP_NAME, path=charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant_backports.any_maintenance(status, MYSQL_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=lambda status: jubilant_backports.all_active(status, MYSQL_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME)


@pytest.mark.abort_on_fail
async def test_fail_and_rollback(juju: Juju, charm: str, continuous_writes) -> None:
    """Test an upgrade failure and its rollback."""
    mysql_app_leader = get_app_leader(juju, MYSQL_APP_NAME)
    mysql_app_units = get_app_units(juju, MYSQL_APP_NAME)

    logging.info("Run pre-upgrade-check action")
    task = juju.run(unit=mysql_app_leader, action="pre-upgrade-check")
    task.raise_on_failure()

    tmp_folder = Path("tmp")
    tmp_folder.mkdir(exist_ok=True)
    tmp_folder_charm = Path(tmp_folder, charm).absolute()

    shutil.copy(charm, tmp_folder_charm)

    logging.info("Inject dependency fault")
    inject_dependency_fault(juju, MYSQL_APP_NAME, tmp_folder_charm)

    logging.info("Refresh the charm")
    juju.refresh(app=MYSQL_APP_NAME, path=tmp_folder_charm)

    logging.info("Wait for upgrade to fail on leader")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes on all units")
    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME, list(mysql_app_units))

    logging.info("Re-run pre-upgrade-check action")
    task = juju.run(unit=mysql_app_leader, action="pre-upgrade-check")
    task.raise_on_failure()

    logging.info("Re-refresh the charm")
    juju.refresh(app=MYSQL_APP_NAME, path=charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant_backports.any_maintenance(status, MYSQL_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=lambda status: jubilant_backports.all_active(status, MYSQL_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes after rollback procedure")
    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME, list(mysql_app_units))

    # Remove fault charm file
    tmp_folder_charm.unlink()


def inject_dependency_fault(juju: Juju, app_name: str, charm_file: str | Path) -> None:
    """Inject a dependency fault into the mysql charm."""
    # Open dependency.json and load current charm version
    with open("src/dependency.json") as dependency_file:
        current_charm_version = json.load(dependency_file)["charm"]["version"]

    # Query running dependency to overwrite with incompatible version
    relation_data = get_relation_data(juju, app_name, "upgrade")

    loaded_dependency_dict = json.loads(relation_data[0]["application-data"]["dependencies"])
    loaded_dependency_dict["charm"]["upgrade_supported"] = f">{current_charm_version}"
    loaded_dependency_dict["charm"]["version"] = f"{int(current_charm_version) + 1}"

    # Overwrite dependency.json with incompatible version
    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        charm_zip.writestr("src/dependency.json", json.dumps(loaded_dependency_dict))
