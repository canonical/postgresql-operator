#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import re
import time

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE,
    METADATA,
    check_patroni,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    restart_patroni,
    run_command_on_unit,
    set_password,
)

APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_deploy_active(ops_test: OpsTest, charm):
    """Build the charm and deploy it."""
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            application_name=APP_NAME,
            num_units=3,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1500)


async def test_password_rotation(ops_test: OpsTest):
    """Test password rotation action."""
    # Get the initial passwords set for the system users.
    superuser_password = await get_password(ops_test)
    replication_password = await get_password(ops_test, "replication")
    monitoring_password = await get_password(ops_test, "monitoring")
    backup_password = await get_password(ops_test, "backup")
    rewind_password = await get_password(ops_test, "rewind")
    patroni_password = await get_password(ops_test, "patroni")

    # Change both passwords.
    await set_password(ops_test, password="test-password")
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    new_superuser_password = await get_password(ops_test)
    assert superuser_password != new_superuser_password

    # For replication, generate a specific password and pass it to the action.
    new_replication_password = "test-password"
    await set_password(ops_test, username="replication", password=new_replication_password)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_replication_password == await get_password(ops_test, "replication")
    assert replication_password != new_replication_password

    # For monitoring, generate a specific password and pass it to the action.
    new_monitoring_password = "test-password"
    await set_password(ops_test, username="monitoring", password=new_monitoring_password)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_monitoring_password == await get_password(ops_test, "monitoring")
    assert monitoring_password != new_monitoring_password

    # For backup, generate a specific password and pass it to the action.
    new_backup_password = "test-password"
    await set_password(ops_test, username="backup", password=new_backup_password)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_backup_password == await get_password(ops_test, "backup")
    assert backup_password != new_backup_password

    # For rewind, generate a specific password and pass it to the action.
    new_rewind_password = "test-password"
    await set_password(ops_test, username="rewind", password=new_rewind_password)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_rewind_password == await get_password(ops_test, "rewind")
    assert rewind_password != new_rewind_password

    # Restart Patroni on any non-leader unit and check that
    # Patroni and PostgreSQL continue to work.
    restart_time = time.time()
    for unit in ops_test.model.applications[APP_NAME].units:
        if not await unit.is_leader_from_status():
            restart_patroni(ops_test, unit.name, patroni_password)
            assert check_patroni(ops_test, unit.name, restart_time)


async def test_db_connection_with_empty_password(ops_test: OpsTest):
    """Test that user can't connect with empty password."""
    primary = await get_primary(ops_test, f"{APP_NAME}/0")
    address = get_unit_address(ops_test, primary)
    with pytest.raises(psycopg2.Error), db_connect(address, "") as connection:
        connection.close()


async def test_no_password_change_on_invalid_password(ops_test: OpsTest) -> None:
    """Test that in general, there is no change when password validation fails."""
    password1 = await get_password(ops_test, username="replication")
    # The password has to be minimum 3 characters
    await set_password(ops_test, username="replication", password="1")
    password2 = await get_password(ops_test, username="replication")
    # The password didn't change
    assert password1 == password2


async def test_no_password_exposed_on_logs(ops_test: OpsTest) -> None:
    """Test that passwords don't get exposed on postgresql logs."""
    for unit in ops_test.model.applications[APP_NAME].units:
        try:
            logs = await run_command_on_unit(
                ops_test,
                unit.name,
                "grep PASSWORD /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log",
            )
        except Exception:
            continue
        regex = re.compile("(PASSWORD )(?!<REDACTED>)")
        logs_without_false_positives = regex.findall(logs)
        assert len(logs_without_false_positives) == 0, (
            f"Sensitive information detected on {unit.name} logs"
        )
