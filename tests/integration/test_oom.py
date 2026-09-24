# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Verify OOM protection on fresh install and a refresh that changes the snap.

Use real VM units: unprivileged LXD containers cannot lower oom_score_adj.
No memory exhaustion or direct workload restart is needed.
"""

import json
import logging
import shlex
import tempfile
import tomllib
import zipfile
from pathlib import Path

import jubilant
import pytest
import tomli_w

from .architecture import architecture
from .helpers import execute_queries_on_unit
from .high_availability.high_availability_helpers_new import (
    get_user_password,
    wait_for_apps_status,
)

logger = logging.getLogger(__name__)

TIMEOUT = 20 * 60
EXISTING_HINT = "oom-test-placeholder"
EXPECTED_HINT = f"{EXISTING_HINT},charmed-postgresql"
EXPECTED_OOM_SCORE_ADJ = -898  # PostgreSQL is second in the seeded vitality hint.
BASELINE_CHARM_REVISION = 1218  # amd64, pre-OOM-protection charm 16/1.374.0
BASELINE_SNAP_REVISION = "406"

pytestmark = pytest.mark.skipif(
    architecture != "amd64", reason="The pinned OOM upgrade baseline is for amd64"
)


@pytest.fixture
def oom_machine(juju: jubilant.Juju) -> str:
    """Allocate a dedicated VM and seed the hint before installing PostgreSQL."""
    previous = set(juju.status().machines)
    juju.add_machine(
        base="ubuntu@24.04",
        constraints={"virt-type": "virtual-machine", "cores": 2, "mem": "2G", "root-disk": "16G"},
    )
    (machine,) = set(juju.status().machines) - previous
    juju.wait(
        lambda status: status.machines[machine].juju_status.current == "started",
        timeout=TIMEOUT,
    )
    juju.exec("systemd-detect-virt --vm", machine=machine)
    # Probe only this short-lived root process; changing the service definition alone
    # can falsely pass in a container. Machine commands run as ubuntu, unlike unit commands.
    juju.exec(
        "sudo -n python3 -c "
        + shlex.quote(
            "from pathlib import Path; "
            f"Path('/proc/self/oom_score_adj').write_text('{EXPECTED_OOM_SCORE_ADJ}')"
        ),
        machine=machine,
    )
    config = json.loads(juju.exec("sudo -n /usr/bin/snap get system -d", machine=machine).stdout)
    assert not config.get("resilience", {}).get("vitality-hint"), config
    juju.exec(
        f"sudo -n /usr/bin/snap set system resilience.vitality-hint={EXISTING_HINT}",
        machine=machine,
    )
    return machine


@pytest.fixture
def baseline_charm(juju: jubilant.Juju, charm: str):
    """Keep published pre-fix code and pin an older snap to exercise workload refresh."""
    assert target_revision(charm) != BASELINE_SNAP_REVISION, "Baseline must change the snap"
    # Juju's snap has a private /tmp; keep download paths in the working directory.
    with tempfile.TemporaryDirectory(prefix=".oom-baseline-", dir=".") as directory:
        original = Path(directory).resolve() / "published.charm"
        baseline = original.with_name("baseline.charm")
        juju.cli(
            "download",
            "postgresql",
            "--revision",
            str(BASELINE_CHARM_REVISION),
            "--no-progress",
            "--filepath",
            str(original),
            include_model=False,
        )
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(baseline, "w") as destination:
            assert "src/oom.py" not in source.namelist(), "Baseline must precede OOM protection"
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == "refresh_versions.toml":
                    versions = tomllib.loads(data.decode())
                    versions["snap"]["revisions"]["x86_64"] = BASELINE_SNAP_REVISION
                    data = tomli_w.dumps(versions).encode()
                destination.writestr(entry, data)
        yield str(baseline)


def target_revision(charm: str) -> str:
    with zipfile.ZipFile(charm) as archive:
        return tomllib.loads(archive.read("refresh_versions.toml").decode())["snap"]["revisions"][
            "x86_64"
        ]


def deploy(juju: jubilant.Juju, charm: str, app: str, machine: str) -> str:
    juju.deploy(charm, app=app, num_units=1, to=machine, config={"profile": "testing"})
    wait_ready(juju, app)
    return f"{app}/0"


def wait_ready(juju: jubilant.Juju, app: str) -> None:
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, app),
        error=lambda status: jubilant.any_error(status, app),
        timeout=TIMEOUT,
        successes=3,
    )


def snapshot(juju: jubilant.Juju, unit: str) -> dict:
    probe = Path(__file__).with_name("oom_probe.py").read_text()
    result = json.loads(juju.exec("python3 -c " + shlex.quote(probe), unit=unit).stdout)
    logger.info("OOM snapshot for %s: %s", unit, json.dumps(result, sort_keys=True))
    return result


def assert_process_scores(state: dict, expected: int) -> None:
    processes = state["processes"]
    assert any(p["pid"] == int(state["service"]["MainPID"]) for p in processes), state
    assert any(p["patroni"] for p in processes), state
    assert sum(p["postmaster"] for p in processes) == 1, state
    assert any(p["name"] == "postgres" and not p["postmaster"] for p in processes), state
    assert int(state["service"]["OOMScoreAdjust"]) == expected, state
    assert all(p["oom_score_adj"] == expected for p in processes), state


def identities(state: dict) -> dict:
    """Use PID and kernel start time to detect replacement, including PID reuse."""
    return {
        role: {(p["pid"], p["start_ticks"]) for p in state["processes"] if p[role]}
        for role in ("patroni", "postmaster")
    }


def query(juju: jubilant.Juju, unit: str, statements: list[str]) -> list:
    app = unit.split("/")[0]
    return execute_queries_on_unit(
        juju.status().apps[app].units[unit].public_address,
        "operator",
        get_user_password(juju, app, "operator"),
        statements,
        "postgres",
    )


def assert_protected(juju: jubilant.Juju, unit: str, revision: str) -> dict:
    state = snapshot(juju, unit)
    assert state["revision"] == revision, state
    # Exact equality checks ordering, preservation, and exactly one appended entry.
    assert state["hint"] == EXPECTED_HINT, state
    assert_process_scores(state, EXPECTED_OOM_SCORE_ADJ)
    # A new SQL connection proves inheritance in a newly forked backend as well.
    assert query(juju, unit, ["SELECT trim(pg_read_file('/proc/self/oom_score_adj'))::int"]) == [
        EXPECTED_OOM_SCORE_ADJ
    ]
    return state


def test_fresh_install(juju: jubilant.Juju, charm: str, oom_machine: str) -> None:
    """Verify fresh installation protects Patroni and PostgreSQL processes."""
    unit = deploy(juju, charm, "postgresql-oom-fresh", oom_machine)
    assert_protected(juju, unit, target_revision(charm))
    assert query(
        juju,
        unit,
        [
            "CREATE TABLE oom_probe (value text PRIMARY KEY)",
            "INSERT INTO oom_probe VALUES ('fresh') RETURNING value",
        ],
    ) == ["fresh"]


def test_snap_refresh(
    juju: jubilant.Juju, charm: str, baseline_charm: str, oom_machine: str
) -> None:
    """Verify snap refresh activates OOM protection and preserves stored data."""
    app = "postgresql-oom-refresh"
    unit = deploy(juju, baseline_charm, app, oom_machine)
    before = snapshot(juju, unit)
    assert before["revision"] == BASELINE_SNAP_REVISION, before
    assert before["hint"] == EXISTING_HINT, before
    assert_process_scores(before, 0)
    assert query(
        juju,
        unit,
        [
            "CREATE TABLE oom_probe (value text PRIMARY KEY)",
            "INSERT INTO oom_probe VALUES ('before-refresh') RETURNING value",
        ],
    ) == ["before-refresh"]

    juju.run(unit=unit, action="pre-refresh-check").raise_on_failure()
    previous_charm_revision = juju.status().apps[app].charm_rev
    juju.refresh(app, path=charm)
    app_ready = wait_for_apps_status(jubilant.all_active, app)

    def refreshed_or_blocked(status: jubilant.Status) -> bool:
        if status.apps[app].charm_rev == previous_charm_revision:
            return False
        if status.apps[app].is_blocked:
            return True
        if not app_ready(status):
            return False
        # Do not accept a stale active status before upgrade-charm has run.
        installed = juju.ssh(unit, "readlink /snap/charmed-postgresql/current").strip()
        return installed == target_revision(charm)

    juju.wait(
        refreshed_or_blocked,
        error=lambda status: jubilant.any_error(status, app),
        timeout=TIMEOUT,
        successes=3,
    )
    if juju.status().apps[app].is_blocked:
        assert "Refresh incompatible" in juju.status().apps[app].app_status.message
        juju.run(
            unit=unit,
            action="force-refresh-start",
            params={"check-compatibility": False},
            wait=TIMEOUT,
        ).raise_on_failure()
    wait_ready(juju, app)

    after = assert_protected(juju, unit, target_revision(charm))
    for role, previous in identities(before).items():
        assert previous.isdisjoint(identities(after)[role]), (before, after)
    assert query(juju, unit, ["SELECT value FROM oom_probe"]) == ["before-refresh"]
    assert query(
        juju,
        unit,
        [
            "INSERT INTO oom_probe VALUES ('after-refresh')",
            "SELECT value FROM oom_probe ORDER BY value",
        ],
    ) == ["after-refresh", "before-refresh"]
