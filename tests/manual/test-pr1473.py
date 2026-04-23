#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""PR #1473 end-to-end test.

Scenario A (existing-test): Deploy 3 units from 16/stable, wait stable,
    refresh to local build, verify the versioned data directory exists as a real directory and
    the migration flag is set, scale-up.
Scenario B (fresh-test): Deploy 3 units directly with local build → verify scale-up.
"""

import json
import os
import platform
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_CHARM_ARCH = "arm64" if platform.machine() == "aarch64" else "amd64"
CHARM = Path(os.environ.get("CHARM_PATH", ROOT / f"postgresql_ubuntu@24.04-{_CHARM_ARCH}.charm"))


def get_target_snap_revision() -> str:
    """Return the pinned snap revision for the current architecture."""
    if override := os.environ.get("TARGET_SNAP_REVISION"):
        return override

    with (ROOT / "refresh_versions.toml").open("rb") as file:
        return tomllib.load(file)["snap"]["revisions"][platform.machine()]


TARGET_SNAP = get_target_snap_revision()
ACTION_WAIT = "15m"
VERSIONED_MOUNTS = [
    "/var/snap/charmed-postgresql/common/var/lib/postgresql",
    "/var/snap/charmed-postgresql/common/data/archive",
    "/var/snap/charmed-postgresql/common/data/logs",
    "/var/snap/charmed-postgresql/common/data/temp",
]


def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    output = result.stdout.strip()
    if result.stderr.strip():
        output = f"{output}\n{result.stderr.strip()}".strip()
    return output, result.returncode


def run_checked(cmd):
    out, rc = run(cmd)
    print(f"  {out}", flush=True)
    if rc != 0 or re.search(r"Action id \d+ failed:", out):
        raise SystemExit(f"Command failed: {cmd}")
    return out


def run_show(cmd):
    subprocess.run(cmd, shell=True, check=True)


def juju_status(model):
    out, _ = run(f"juju status -m {model} --format=json")
    return json.loads(out)


def juju_show_unit(model, unit):
    out, _ = run(f"juju show-unit -m {model} {unit} --format=json")
    return json.loads(out)[unit]


def get_postgresql_units(model, *, reverse=False):
    units = juju_status(model)["applications"]["postgresql"]["units"]
    return sorted(units, key=lambda unit: int(unit.split("/")[1]), reverse=reverse)


def get_active_idle(model, n):
    try:
        units = juju_status(model)["applications"]["postgresql"]["units"]
        total = len(units)
        active_idle = sum(
            1
            for u in units.values()
            if u["workload-status"]["current"] == "active"
            and u["juju-status"]["current"] == "idle"
        )
        return active_idle, total
    except Exception:
        return 0, 0


def wait_n_active(model, n, timeout=1800):
    start = time.time()
    while time.time() - start < timeout:
        ai, total = get_active_idle(model, n)
        print(f"  [{model}] {ai}/{n} active-idle (total={total})", flush=True)
        if ai >= n and total == n:
            return True
        time.sleep(30)
    print(f"  [{model}] TIMEOUT waiting for {n} active-idle", flush=True)
    return False


def get_snap_revision(model, unit):
    out, rc = run(f"juju ssh -m {model} {unit} -- sudo readlink /snap/charmed-postgresql/current")
    return Path(out.strip()).name if rc == 0 and out else None


def wait_unit_snap(model, unit, snap, timeout=1800):
    start = time.time()
    while time.time() - start < timeout:
        rev = get_snap_revision(model, unit)
        ai, total = get_active_idle(model, 3)
        print(f"  [{model}/{unit}] snap={rev} cluster={ai}/{total} active-idle", flush=True)
        if rev == snap:
            print(f"  [{model}/{unit}] ✓ on snap {snap}", flush=True)
            return True
        time.sleep(30)
    print(f"  [{model}/{unit}] TIMEOUT waiting for snap {snap}", flush=True)
    return False


def wait_refresh_metadata(model, observer_unit, refreshed_unit, snap, timeout=1800):
    start = time.time()
    while time.time() - start < timeout:
        relation = next(
            relation
            for relation in juju_show_unit(model, observer_unit)["relation-info"]
            if relation["endpoint"] == "refresh-v-three"
        )
        local_refresh = json.loads(
            relation["local-unit"]["data"]["last_refresh_to_up_to_date_charm_code_version"]
        )
        related_refresh = relation["related-units"].get(refreshed_unit, {}).get("data", {})
        related_snap_raw = related_refresh.get("installed_snap_revision")
        related_last_refresh_raw = related_refresh.get(
            "last_refresh_to_up_to_date_charm_code_version"
        )
        related_snap = json.loads(related_snap_raw) if related_snap_raw else None
        related_last_refresh = (
            json.loads(related_last_refresh_raw) if related_last_refresh_raw else {}
        )
        ready = (
            related_snap == snap
            and related_last_refresh.get("charm_revision") == local_refresh["charm_revision"]
        )
        print(
            f"  [{model}/{observer_unit}] sees {refreshed_unit} snap={related_snap} "
            f"metadata-ready={ready}",
            flush=True,
        )
        if ready:
            return True
        time.sleep(30)
    print(
        f"  [{model}/{observer_unit}] TIMEOUT waiting for {refreshed_unit} refresh metadata",
        flush=True,
    )
    return False


def wait_cluster_members_ready(model, timeout=1800):
    start = time.time()
    while time.time() - start < timeout:
        out, rc = run(
            " ".join([
                f"juju ssh -m {model} postgresql/leader --",
                "sudo patronictl",
                "-c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml",
                "list --format json",
            ])
        )
        if rc == 0:
            members = json.loads(out)
            states = [member["State"] for member in members]
            ready = all(state in {"running", "streaming"} for state in states)
            print(f"  [{model}] patroni states={states} ready={ready}", flush=True)
            if ready:
                return True
        else:
            print(f"  [{model}] patroni health check unavailable", flush=True)
        time.sleep(30)
    print(f"  [{model}] TIMEOUT waiting for Patroni cluster to be fully ready", flush=True)
    return False


def run_resume_refresh(model, unit, timeout=1800):
    current_unit = unit
    start = time.time()
    while time.time() - start < timeout:
        command = f"juju run -m {model} {current_unit} resume-refresh --wait={ACTION_WAIT}"
        out, rc = run(command)
        print(f"  {out}", flush=True)
        if rc == 0 and not re.search(r"Action id \d+ failed:", out):
            return

        match = re.search(r"Action id \d+ failed: Must run action on unit (\d+)", out)
        if match:
            current_unit = f"postgresql/{match.group(1)}"
            print(
                f"  Retrying resume-refresh on {current_unit} after propagation delay", flush=True
            )
            time.sleep(30)
            continue

        if wait_unit_snap(model, current_unit, TARGET_SNAP, timeout=300):
            print(
                f"  {current_unit} reached snap {TARGET_SNAP} despite resume-refresh reporting a failure",
                flush=True,
            )
            return

        raise SystemExit(f"Command failed: {command}")

    raise SystemExit("Timed out waiting for resume-refresh to become runnable")


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def has_real_directory(model, unit):
    # Use individual juju exec invocations with `test` builtin. This avoids all shell quoting
    # issues: juju exec passes args directly to exec() without a shell, so no bracket [ ] or
    # && quoting needed.
    for path in VERSIONED_MOUNTS:
        versioned = f"{path}/16/main"
        _, rc = run(f"juju exec -m {model} --unit {unit} -- test -d {versioned}")
        if rc != 0:
            return False
        # test -L returns 0 if it IS a symlink — we want NOT a symlink, so rc should be 1
        _, rc = run(f"juju exec -m {model} --unit {unit} -- test -L {versioned}")
        if rc == 0:
            return False
    return True


def find_storage_layout_flag(value: Any) -> bool:
    """Recursively find the migration flag in `juju show-unit --format=json` output."""
    if isinstance(value, dict):
        if value.get("storage_layout_migrated") == "True":
            return True
        return any(find_storage_layout_flag(item) for item in value.values())
    if isinstance(value, list):
        return any(find_storage_layout_flag(item) for item in value)
    return False


def has_storage_layout_flag(model, unit):
    out, rc = run(f"juju show-unit -m {model} {unit} --format=json")
    if rc != 0:
        return False
    return find_storage_layout_flag(json.loads(out))


if not CHARM.exists():
    raise SystemExit(f"Charm not found: {CHARM}")

# ---------------------------------------------------------------------------
print("=== Deploying models ===", flush=True)
run_show("juju add-model existing-test")
run_show("juju add-model fresh-test")
if platform.machine() == "aarch64":
    run_show("juju set-model-constraints -m existing-test arch=arm64")
    run_show("juju set-model-constraints -m fresh-test arch=arm64")
run_show("juju deploy -m existing-test postgresql --channel=16/stable -n 3 --base ubuntu@24.04")
run_show(f"juju deploy -m fresh-test {CHARM} -n 3 --base ubuntu@24.04")

# ---------------------------------------------------------------------------
print("\n=== Phase 1: wait existing-test 3/3 stable ===", flush=True)
require(wait_n_active("existing-test", 3), "existing-test did not reach 3/3 active-idle")

# ---------------------------------------------------------------------------
print("\n=== Phase 2: refresh existing-test ===", flush=True)
run_show(f"juju refresh -m existing-test postgresql --path={CHARM}")

# ---------------------------------------------------------------------------
print("\n=== Phase 3: force-refresh-start on postgresql/2 ===", flush=True)
# Charm_refresh blocks "unreleased" local builds; bypass with check-compatibility=false.
# Wait briefly for the charm to settle after refresh before forcing.
time.sleep(30)
run_checked(
    f"juju run -m existing-test postgresql/2 force-refresh-start --wait={ACTION_WAIT} "
    "check-compatibility=false"
)

# ---------------------------------------------------------------------------
# Rolling refresh: charm_refresh upgrades one unit at a time, highest unit first.
refresh_order = get_postgresql_units("existing-test", reverse=True)
print(f"\n=== Phase 4: wait rolling refresh ({', '.join(refresh_order)}) ===", flush=True)
require(
    wait_unit_snap("existing-test", refresh_order[0], TARGET_SNAP),
    f"{refresh_order[0]} did not reach snap {TARGET_SNAP}",
)

remaining_units = [
    unit for unit in refresh_order if get_snap_revision("existing-test", unit) != TARGET_SNAP
]
if remaining_units:
    require(
        wait_refresh_metadata("existing-test", remaining_units[0], refresh_order[0], TARGET_SNAP),
        f"{remaining_units[0]} did not observe {refresh_order[0]} refresh metadata",
    )
    print(f"\n=== Phase 4b: resume refresh on {remaining_units[0]} ===", flush=True)
    run_resume_refresh("existing-test", remaining_units[0])
    for unit in remaining_units:
        require(
            wait_unit_snap("existing-test", unit, TARGET_SNAP),
            f"{unit} did not reach snap {TARGET_SNAP}",
        )

# ---------------------------------------------------------------------------
print("\n=== Phase 5: wait existing-test 3/3 post-refresh ===", flush=True)
require(wait_n_active("existing-test", 3), "existing-test did not return to 3/3 active-idle")

# ---------------------------------------------------------------------------
print("\n=== Phase 6: wait fresh-test 3/3 ===", flush=True)
require(wait_n_active("fresh-test", 3), "fresh-test did not reach 3/3 active-idle")

# ---------------------------------------------------------------------------
print("\n=== Phase 6b: wait both clusters fully ready ===", flush=True)
require(
    wait_cluster_members_ready("existing-test"),
    "existing-test cluster did not become fully ready before scale-up",
)
require(
    wait_cluster_members_ready("fresh-test"),
    "fresh-test cluster did not become fully ready before scale-up",
)

# ---------------------------------------------------------------------------
print("\n=== Phase 7: scale up both ===", flush=True)
run_show("juju add-unit -m existing-test postgresql")
run_show("juju add-unit -m fresh-test postgresql")

# ---------------------------------------------------------------------------
print("\n=== Phase 8: wait both 4/4 ===", flush=True)
existing_ok = wait_n_active("existing-test", 4)
fresh_ok = wait_n_active("fresh-test", 4)

# ---------------------------------------------------------------------------
# Check directories only after full rolling refresh + scale-up are complete.
print("\n=== Phase 9: verify versioned data directories ===", flush=True)
paths_ok = True
flags_ok = True

for model in ["existing-test", "fresh-test"]:
    for unit in get_postgresql_units(model):
        if has_real_directory(model, unit):
            print(f"  [PASS] {model}/{unit}: versioned data directory is a real dir", flush=True)
        else:
            print(f"  [FAIL] {model}/{unit}: missing versioned data directory", flush=True)
            paths_ok = False
        if has_storage_layout_flag(model, unit):
            print(f"  [PASS] {model}/{unit}: storage_layout_migrated flag set", flush=True)
        else:
            print(f"  [FAIL] {model}/{unit}: storage_layout_migrated flag missing", flush=True)
            flags_ok = False

# ---------------------------------------------------------------------------
print("\n=== Results ===", flush=True)
all_ok = paths_ok and flags_ok and existing_ok and fresh_ok
if all_ok:
    print("PASSED ✓", flush=True)
    sys.exit(0)
else:
    print(
        f"FAILED: paths={paths_ok} flags={flags_ok} existing={existing_ok} fresh={fresh_ok}",
        flush=True,
    )
    sys.exit(1)
