#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import re
import time

import psycopg2
import pytest

from .adapters import JujuFixture
from .jubilant_helpers import (
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
def test_deploy_active(juju: JujuFixture, charm):
    """Build the charm and deploy it."""
    with juju.ext.fast_forward():
        juju.ext.model.deploy(
            charm,
            application_name=APP_NAME,
            num_units=3,
            config={"profile": "testing"},
        )
        juju.ext.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1500)


def test_password_rotation(juju: JujuFixture):
    """Test password rotation action."""
    # Get the initial passwords set for the system users.
    superuser_password = get_password()
    replication_password = get_password("replication")
    monitoring_password = get_password("monitoring")
    backup_password = get_password("backup")
    rewind_password = get_password("rewind")
    patroni_password = get_password("patroni")

    # Change both passwords.
    set_password(juju, password="test-password")
    juju.ext.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    new_superuser_password = get_password()
    assert superuser_password != new_superuser_password

    # For replication, generate a specific password and pass it to the action.
    new_replication_password = "test-password"
    set_password(juju, username="replication", password=new_replication_password)
    juju.ext.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_replication_password == get_password("replication")
    assert replication_password != new_replication_password

    # For monitoring, generate a specific password and pass it to the action.
    new_monitoring_password = "test-password"
    set_password(juju, username="monitoring", password=new_monitoring_password)
    juju.ext.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_monitoring_password == get_password("monitoring")
    assert monitoring_password != new_monitoring_password

    # For backup, generate a specific password and pass it to the action.
    new_backup_password = "test-password"
    set_password(juju, username="backup", password=new_backup_password)
    juju.ext.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_backup_password == get_password("backup")
    assert backup_password != new_backup_password

    # For rewind, generate a specific password and pass it to the action.
    new_rewind_password = "test-password"
    set_password(juju, username="rewind", password=new_rewind_password)
    juju.ext.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert new_rewind_password == get_password("rewind")
    assert rewind_password != new_rewind_password

    # Restart Patroni on any non-leader unit and check that
    # Patroni and PostgreSQL continue to work.
    restart_time = time.time()
    for unit in juju.ext.model.applications[APP_NAME].units:
        if not unit.is_leader_from_status():
            restart_patroni(juju, unit.name, patroni_password)
            assert check_patroni(juju, unit.name, restart_time)


def test_db_connection_with_empty_password(juju: JujuFixture):
    """Test that user can't connect with empty password."""
    primary = get_primary(juju, f"{APP_NAME}/0")
    address = get_unit_address(juju, primary)
    with pytest.raises(psycopg2.Error), db_connect(address, "") as connection:
        connection.close()


def test_no_password_change_on_invalid_password(juju: JujuFixture) -> None:
    """Test that in general, there is no change when password validation fails."""
    password1 = get_password(username="replication")
    # The password has to be minimum 3 characters
    set_password(juju, username="replication", password="1")
    password2 = get_password(username="replication")
    # The password didn't change
    assert password1 == password2


def test_no_password_exposed_on_logs(juju: JujuFixture) -> None:
    """Test that passwords don't get exposed on postgresql logs."""
    for unit in juju.ext.model.applications[APP_NAME].units:
        try:
            logs = run_command_on_unit(
                juju,
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
