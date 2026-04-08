#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess

import jubilant
import pytest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .helpers import DATABASE_APP_NAME
from .jubilant_helpers import get_primary

logger = logging.getLogger(__name__)

MINUTE = 60
TIMEOUT = 20 * MINUTE
REINIT_TIMEOUT = 20 * MINUTE

PATRONI_CONFIGURATION_FILE = "/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml"
PG_CONTROL_FILE = (
    "/var/snap/charmed-postgresql/common/var/lib/postgresql/16/main/global/pg_control"
)

LAYOUT_DIRECTORIES = (
    "/var/snap/charmed-postgresql/common/var/lib/postgresql/16/main",
    "/var/snap/charmed-postgresql/common/data/logs/16/main/pg_wal",
    "/var/snap/charmed-postgresql/common/data/temp/16/main/pgsql_tmp",
    "/var/snap/charmed-postgresql/common/data/archive/16/main",
)


def _run_juju_ssh(juju: jubilant.Juju, unit_name: str, remote_command: str) -> str:
    """Run a command over `juju ssh` and return stdout."""
    result = subprocess.run(
        ["juju", "ssh", "-m", juju.model, unit_name, remote_command],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): juju ssh -m {juju.model} {unit_name}"
            f" {remote_command}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def _patronictl_list(juju: jubilant.Juju, unit_name: str) -> str:
    """Return the Patroni cluster table from a unit."""
    return _run_juju_ssh(
        juju,
        unit_name,
        f"sudo charmed-postgresql.patronictl -c {PATRONI_CONFIGURATION_FILE} list",
    )


def _member_role_state(patronictl_output: str, member_name: str) -> tuple[str | None, str | None]:
    """Return role and state for a member in `patronictl list` output."""
    for raw_line in patronictl_output.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or member_name not in line:
            continue

        columns = [column.strip() for column in raw_line.split("|")[1:-1]]
        if len(columns) < 4:
            continue

        role = columns[2]
        state = columns[3].lower()
        return role, state

    return None, None


@pytest.mark.abort_on_fail
def test_deploy_three_units(juju: jubilant.Juju, charm) -> None:
    """Deploy a three-unit PostgreSQL cluster."""
    if DATABASE_APP_NAME not in juju.status().apps:
        juju.deploy(
            charm,
            app=DATABASE_APP_NAME,
            num_units=3,
            config={"profile": "testing"},
        )

    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE)

    unit_names = sorted(juju.status().get_units(DATABASE_APP_NAME))
    assert len(unit_names) == 3, f"Expected 3 units, got {len(unit_names)}: {unit_names}"


def test_reinit_after_pg_control_removal(juju: jubilant.Juju) -> None:
    """Remove pg_control on a replica and verify `patronictl reinit` recovers it."""
    unit_names = sorted(juju.status().get_units(DATABASE_APP_NAME))
    assert len(unit_names) == 3, f"Expected 3 units, got {len(unit_names)}: {unit_names}"

    primary_unit = get_primary(juju, unit_names[0])
    replica_unit = next(unit for unit in unit_names if unit != primary_unit)
    replica_member = replica_unit.replace("/", "-")

    logger.info("Primary: %s, replica under test: %s", primary_unit, replica_unit)

    for layout_path in LAYOUT_DIRECTORIES:
        _run_juju_ssh(juju, primary_unit, f"sudo test -d {layout_path}")

    _run_juju_ssh(
        juju,
        replica_unit,
        f"sudo rm -f {PG_CONTROL_FILE} && sudo test ! -f {PG_CONTROL_FILE}",
    )
    _run_juju_ssh(juju, replica_unit, "sudo snap restart charmed-postgresql.patroni")

    for attempt in Retrying(stop=stop_after_delay(5 * MINUTE), wait=wait_fixed(10), reraise=True):
        with attempt:
            cluster_table = _patronictl_list(juju, primary_unit)
            _, state = _member_role_state(cluster_table, replica_member)
            assert state == "stopped", (
                f"Expected {replica_member} to be stopped after pg_control removal.\n{cluster_table}"
            )

    reinit_command = " ".join([
        f"sudo charmed-postgresql.patronictl -c {PATRONI_CONFIGURATION_FILE} reinit",
        f"{DATABASE_APP_NAME} {replica_member} --force --wait",
    ])

    reinit_output = ""
    for attempt in Retrying(stop=stop_after_delay(5 * MINUTE), wait=wait_fixed(10), reraise=True):
        with attempt:
            reinit_output = _run_juju_ssh(juju, primary_unit, reinit_command)

    assert "Success: reinitialize for member" in reinit_output
    assert "Reinitialize is completed on" in reinit_output

    for attempt in Retrying(
        stop=stop_after_delay(REINIT_TIMEOUT),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            _run_juju_ssh(juju, replica_unit, f"sudo test -f {PG_CONTROL_FILE}")
            cluster_table = _patronictl_list(juju, primary_unit)
            role, state = _member_role_state(cluster_table, replica_member)
            assert role in {"Replica", "Sync Standby"}, (
                f"Unexpected role for {replica_member}: {role}\n{cluster_table}"
            )
            assert state in {"running", "streaming"}, (
                f"Unexpected state for {replica_member}: {state}\n{cluster_table}"
            )

    juju.wait(lambda status: jubilant.all_active(status, DATABASE_APP_NAME), timeout=TIMEOUT)
