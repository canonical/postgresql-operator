#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""PR #1473 end-to-end test.

Scenario A (existing-test): Deploy 3 units from 16/stable, wait stable,
    refresh to local build → verify symlinks on all 3 original units, scale-up.
Scenario B (fresh-test): Deploy 3 units directly with local build → verify scale-up.
"""

import json
import subprocess
import sys
import time

CHARM = "/home/neppel/postgresql-operator-6/postgresql_ubuntu@24.04-amd64.charm"
TARGET_SNAP = "268"


def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.returncode


def run_show(cmd):
    subprocess.run(cmd, shell=True)


def juju_status(model):
    out, _ = run(f"juju status -m {model} --format=json")
    return json.loads(out)


def get_active_idle(model, n):
    try:
        status = juju_status(model)
        units = status["applications"]["postgresql"]["units"]
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
    out, rc = run(
        f"juju exec -m {model} --unit {unit} -- readlink /snap/charmed-postgresql/current"
    )
    return out.strip() if rc == 0 else None


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


def get_symlink(model, unit):
    out, rc = run(
        f"juju exec -m {model} --unit {unit} -- "
        f"readlink /var/snap/charmed-postgresql/common/var/lib/postgresql/16/main"
    )
    return out.strip() if rc == 0 else None


# ---------------------------------------------------------------------------
print("=== Deploying models ===", flush=True)
run_show("juju add-model existing-test")
run_show("juju add-model fresh-test")
run_show("juju deploy -m existing-test postgresql --channel=16/stable -n 3")
run_show(f"juju deploy -m fresh-test {CHARM} -n 3")

# ---------------------------------------------------------------------------
print("\n=== Phase 1: wait existing-test 3/3 stable ===", flush=True)
wait_n_active("existing-test", 3)

# ---------------------------------------------------------------------------
print("\n=== Phase 2: refresh existing-test ===", flush=True)
run_show(f"juju refresh -m existing-test postgresql --path={CHARM}")

# ---------------------------------------------------------------------------
print("\n=== Phase 3: force-refresh-start on postgresql/2 ===", flush=True)
# Charm_refresh blocks "unreleased" local builds; bypass with check-compatibility=false.
# Wait briefly for the charm to settle after refresh before forcing.
time.sleep(30)
out, _ = run(
    "juju run -m existing-test postgresql/2 force-refresh-start check-compatibility=false"
)
print(f"  {out}", flush=True)

# ---------------------------------------------------------------------------
# Rolling refresh: charm_refresh upgrades one unit at a time (2 → 1 → 0).
print("\n=== Phase 4: wait rolling refresh (units 2, 1, 0) ===", flush=True)
for unit_num in [2, 1, 0]:
    wait_unit_snap("existing-test", f"postgresql/{unit_num}", TARGET_SNAP)

# ---------------------------------------------------------------------------
print("\n=== Phase 5: wait existing-test 3/3 post-refresh ===", flush=True)
wait_n_active("existing-test", 3)

# ---------------------------------------------------------------------------
print("\n=== Phase 6: wait fresh-test 3/3 ===", flush=True)
wait_n_active("fresh-test", 3)

# ---------------------------------------------------------------------------
print("\n=== Phase 7: scale up both ===", flush=True)
run_show("juju add-unit -m existing-test postgresql")
run_show("juju add-unit -m fresh-test postgresql")

# ---------------------------------------------------------------------------
print("\n=== Phase 8: wait both 4/4 ===", flush=True)
existing_ok = wait_n_active("existing-test", 4)
fresh_ok = wait_n_active("fresh-test", 4)

# ---------------------------------------------------------------------------
# Check symlinks only after full rolling refresh + scale-up are complete.
print("\n=== Phase 9: verify symlinks ===", flush=True)
symlinks_ok = True

# existing-test units 0-2: refreshed from 16/stable → should have symlink
for i in [0, 1, 2]:
    unit = f"postgresql/{i}"
    symlink = get_symlink("existing-test", unit)
    if symlink:
        print(f"  [PASS] existing-test/{unit}: -> {symlink}", flush=True)
    else:
        print(f"  [FAIL] existing-test/{unit}: no symlink", flush=True)
        symlinks_ok = False

# existing-test unit 3 (scale-up): fresh join → real dir, no symlink expected
symlink = get_symlink("existing-test", "postgresql/3")
if not symlink:
    print(
        "  [PASS] existing-test/postgresql/3: real dir (scale-up unit, no symlink expected)",
        flush=True,
    )
else:
    print(f"  [INFO] existing-test/postgresql/3: symlink={symlink}", flush=True)

# fresh-test: all units are fresh deploys → real dirs, no symlinks expected
for i in [0, 1, 2, 3]:
    unit = f"postgresql/{i}"
    symlink = get_symlink("fresh-test", unit)
    if not symlink:
        print(
            f"  [PASS] fresh-test/{unit}: real dir (fresh deploy, no symlink expected)", flush=True
        )
    else:
        print(f"  [INFO] fresh-test/{unit}: symlink={symlink}", flush=True)

# ---------------------------------------------------------------------------
print("\n=== Results ===", flush=True)
all_ok = symlinks_ok and existing_ok and fresh_ok
if all_ok:
    print("PASSED ✓", flush=True)
    sys.exit(0)
else:
    print(
        f"FAILED: symlinks={symlinks_ok} existing={existing_ok} fresh={fresh_ok}",
        flush=True,
    )
    sys.exit(1)
