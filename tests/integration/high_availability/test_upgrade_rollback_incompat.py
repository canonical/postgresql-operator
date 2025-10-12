# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import shutil
import time
import zipfile
from ast import literal_eval
from collections.abc import Generator
from pathlib import Path

import jubilant
import pytest
from jubilant import Juju

from ..markers import amd64_only
from .high_availability_helpers_new import (
    check_mysql_units_writes_increment,
    get_app_leader,
    get_relation_data,
    get_unit_by_number,
    get_unit_status_log,
    wait_for_apps_status,
    wait_for_unit_status,
)

MYSQL_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture()
def continuous_writes(juju: Juju) -> Generator:
    """Starts continuous writes to the MySQL cluster for a test and clear the writes at the end."""
    test_app_leader = get_app_leader(juju, MYSQL_TEST_APP_NAME)

    logging.info("Clearing continuous writes")
    juju.run(test_app_leader, "clear-continuous-writes")
    logging.info("Starting continuous writes")
    juju.run(test_app_leader, "start-continuous-writes")

    yield

    logging.info("Clearing continuous writes")
    juju.run(test_app_leader, "clear-continuous-writes")


# TODO: remove AMD64 marker after next incompatible MySQL server version is released in our snap
# (details: https://github.com/canonical/mysql-operator/pull/472#discussion_r1659300069)
@amd64_only
@pytest.mark.abort_on_fail
async def test_build_and_deploy(juju: Juju, charm: str) -> None:
    """Simple test to ensure that the MySQL and application charms get deployed."""
    snap_revisions = Path("snap_revisions.json")
    with snap_revisions.open("r") as file:
        old_revisions = json.load(file)

    # TODO: support arm64 & s390x
    new_revisions = old_revisions.copy()
    new_revisions["x86_64"] = "69"

    with snap_revisions.open("w") as file:
        json.dump(new_revisions, file)

    local_charm = get_locally_built_charm(charm)

    with snap_revisions.open("w") as file:
        json.dump(old_revisions, file)

    juju.deploy(
        charm=local_charm,
        app=MYSQL_APP_NAME,
        base="ubuntu@22.04",
        config={"profile": "testing", "plugin-audit-enabled": False},
        num_units=3,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        config={"auto_start_writes": False, "sleep_interval": 500},
        num_units=1,
    )

    juju.integrate(
        f"{MYSQL_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_APP_NAME, MYSQL_TEST_APP_NAME),
        error=jubilant.any_blocked,
        timeout=20 * MINUTE_SECS,
    )


# TODO: remove AMD64 marker after next incompatible MySQL server version is released in our snap
# (details: https://github.com/canonical/mysql-operator/pull/472#discussion_r1659300069)
@amd64_only
@pytest.mark.abort_on_fail
async def test_pre_upgrade_check(juju: Juju) -> None:
    """Test that the pre-upgrade-check action runs successfully."""
    mysql_leader = get_app_leader(juju, MYSQL_APP_NAME)

    logging.info("Run pre-upgrade-check action")
    task = juju.run(unit=mysql_leader, action="pre-upgrade-check")
    task.raise_on_failure()


# TODO: remove AMD64 marker after next incompatible MySQL server version is released in our snap
# (details: https://github.com/canonical/mysql-operator/pull/472#discussion_r1659300069)
@amd64_only
@pytest.mark.abort_on_fail
async def test_upgrade_to_failing(juju: Juju, charm: str, continuous_writes) -> None:
    logging.info("Ensure continuous_writes")
    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME)

    with InjectFailure(
        path="src/upgrade.py",
        original_str="self.charm.recover_unit_after_restart()",
        replace_str="raise Exception",
    ):
        logging.info("Build charm with failure injected")
        new_charm = get_locally_built_charm(charm)

    logging.info("Refresh the charm")
    juju.refresh(app=MYSQL_APP_NAME, path=new_charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant.any_maintenance(status, MYSQL_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Get first upgrading unit")
    relation_data = get_relation_data(juju, MYSQL_APP_NAME, "upgrade")
    upgrade_stack = relation_data[0]["application-data"]["upgrade-stack"]
    upgrade_unit = get_unit_by_number(juju, MYSQL_APP_NAME, literal_eval(upgrade_stack)[-1])

    logging.info("Wait for upgrade to fail on upgrading unit")
    juju.wait(
        ready=wait_for_unit_status(MYSQL_APP_NAME, upgrade_unit, "blocked"),
        timeout=10 * MINUTE_SECS,
    )


# TODO: remove AMD64 marker after next incompatible MySQL server version is released in our snap
# (details: https://github.com/canonical/mysql-operator/pull/472#discussion_r1659300069)
@amd64_only
@pytest.mark.abort_on_fail
async def test_rollback(juju: Juju, charm: str, continuous_writes) -> None:
    """Test upgrade rollback to a healthy revision."""
    relation_data = get_relation_data(juju, MYSQL_APP_NAME, "upgrade")
    upgrade_stack = relation_data[0]["application-data"]["upgrade-stack"]
    upgrade_unit = get_unit_by_number(juju, MYSQL_APP_NAME, literal_eval(upgrade_stack)[-1])

    snap_revisions = Path("snap_revisions.json")
    with snap_revisions.open("r") as file:
        old_revisions = json.load(file)

    # TODO: support arm64 & s390x
    new_revisions = old_revisions.copy()
    new_revisions["x86_64"] = "69"

    with snap_revisions.open("w") as file:
        json.dump(new_revisions, file)

    mysql_leader = get_app_leader(juju, MYSQL_APP_NAME)
    local_charm = get_locally_built_charm(charm)

    time.sleep(10)

    logging.info("Run pre-upgrade-check action")
    task = juju.run(unit=mysql_leader, action="pre-upgrade-check")
    task.raise_on_failure()

    time.sleep(20)

    logging.info("Refresh with previous charm")
    juju.refresh(app=MYSQL_APP_NAME, path=local_charm)

    logging.info("Wait for upgrade to start")
    juju.wait(
        ready=lambda status: jubilant.any_maintenance(status, MYSQL_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )
    juju.wait(
        ready=lambda status: jubilant.all_active(status, MYSQL_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure rollback has taken place")
    unit_status_logs = get_unit_status_log(juju, upgrade_unit, 100)

    upgrade_failed_index = get_unit_log_message(
        status_logs=unit_status_logs[:],
        unit_message="upgrade failed. Check logs for rollback instruction",
    )
    assert upgrade_failed_index is not None

    upgrade_complete_index = get_unit_log_message(
        status_logs=unit_status_logs[upgrade_failed_index:],
        unit_message="upgrade completed",
    )
    assert upgrade_complete_index is not None

    logging.info("Ensure continuous writes after rollback procedure")
    await check_mysql_units_writes_increment(juju, MYSQL_APP_NAME)


class InjectFailure:
    def __init__(self, path: str, original_str: str, replace_str: str):
        self.path = path
        self.original_str = original_str
        self.replace_str = replace_str
        with open(path) as file:
            self.original_content = file.read()

    def __enter__(self):
        """Inject failure context."""
        logging.info("Injecting failure")
        assert self.original_str in self.original_content, "replace content not found"
        new_content = self.original_content.replace(self.original_str, self.replace_str)
        assert self.original_str not in new_content, "original string not replaced"
        with open(self.path, "w") as file:
            file.write(new_content)

    def __exit__(self, exc_type, exc_value, traceback):
        """Inject failure context."""
        logging.info("Reverting failure")
        with open(self.path, "w") as file:
            file.write(self.original_content)


def get_unit_log_message(status_logs: list[dict], unit_message: str) -> int | None:
    """Returns the index of a status log containing the desired message."""
    for index, status_log in enumerate(status_logs):
        if status_log.get("message") == unit_message:
            return index

    return None


def get_locally_built_charm(charm: str) -> str:
    """Wrapper for a local charm build zip file updating."""
    local_charm_paths = Path().glob("local-*.charm")

    # Clean up local charms from previous runs
    # to avoid pytest_operator_cache globbing them
    for charm_path in local_charm_paths:
        charm_path.unlink()

    # Create a copy of the charm to avoid modifying the original
    local_charm_path = shutil.copy(charm, f"local-{Path(charm).stem}.charm")
    local_charm_path = Path(local_charm_path)

    for path in ["snap_revisions.json", "src/upgrade.py"]:
        with open(path) as f:
            content = f.read()
        with zipfile.ZipFile(local_charm_path, mode="a") as charm_zip:
            charm_zip.writestr(path, content)

    return f"{local_charm_path.resolve()}"
