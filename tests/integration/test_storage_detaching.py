#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import jubilant
import pytest

from .adapters import JujuFixture
from .jubilant_helpers import DATABASE_APP_NAME

logger = logging.getLogger(__name__)

# rootfs (machine-scoped) is the only pool that reproduces the stuck unmount on
# Juju 4.0: the detachable lxd pool destroys the container before detaching.
ROOTFS_STORAGE = dict.fromkeys(("archive", "data", "logs", "temp"), "rootfs")


@pytest.mark.abort_on_fail
def test_storage_released_on_scale_down(juju: JujuFixture, charm):
    """Scale-down (remove-unit) must release the departing units' storage.

    Regression test for canonical/postgresql-operator#1550 on the scale-down
    path. Removing two units at once also exercises dropping each departing unit
    from raft via a surviving peer before it stops (the first peer tried may
    itself be departing); a minority removal keeps quorum, so the survivors must
    settle back to active.
    """
    juju.ext.model.deploy(
        charm,
        num_units=4,
        config={"profile": "testing"},
        storage=ROOTFS_STORAGE,
        force=True,
    )
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    doomed = {unit.name for unit in juju.ext.model.applications[DATABASE_APP_NAME].units[-2:]}
    logger.info("Removing %s at once; the two survivors must keep quorum", sorted(doomed))
    for unit in sorted(doomed):
        juju.ext.model.destroy_unit(unit, destroy_storage=True)

    # Concurrent removal on Juju 4.0 can transiently error a departing unit on a canceled
    # hook ("context canceled" as the uniter tears it down) and stall its removal. Resolve
    # those on the departing units — benign, they are going away — and keep waiting; a
    # survivor erroring is a real failure. The #1550 symptom is storage stuck "detaching".
    resolved: set[str] = set()
    deadline = time.monotonic() + 15 * 60
    while time.monotonic() < deadline:
        units = juju.status().apps[DATABASE_APP_NAME].units or {}
        for name, unit in units.items():
            if not unit.is_error:
                continue
            if name in doomed:
                if name not in resolved:
                    logger.info(
                        "resolving transient error on departing %s: %s",
                        name,
                        unit.workload_status.message,
                    )
                    try:
                        juju.cli("resolve", name)
                    except jubilant.CLIError:
                        # Benign race: the unit's state changed between the status check
                        # and resolve (already removed, or no longer in error). The poll
                        # keeps waiting; a genuinely stuck removal fails on the timeout.
                        logger.debug("resolve no-op for %s (state changed)", name)
                    resolved.add(name)
            else:
                raise AssertionError(
                    f"survivor {name} errored during scale-down: {unit.workload_status.message}"
                )
        if len(units) <= 2 and all(u.is_active for u in units.values()):
            break
        time.sleep(10)
    units = juju.status().apps[DATABASE_APP_NAME].units or {}
    assert len(units) == 2, f"expected 2 survivors after scale-down, got {sorted(units)}"
    assert all(u.is_active for u in units.values()), (
        f"survivors not active: {[(n, u.workload_status.current) for n, u in units.items()]}"
    )

    detaching = [s for s in juju.ext.model.list_storage() if s["life"] == "detaching"]
    assert not detaching, f"storage stuck detaching after scale-down: {detaching}"


@pytest.mark.abort_on_fail
def test_storage_released_on_removal(juju: JujuFixture):
    """Full removal must release the storage so teardown completes.

    Regression test for canonical/postgresql-operator#1550: without stopping the
    workload in the ``storage-detaching`` hook the snap keeps the storage mounts
    busy, so Juju's unmount fails ("target is busy") and teardown hangs. Reuses
    the two-unit cluster the scale-down test above leaves behind.
    """
    logger.info("Removing %s and waiting for a clean teardown", DATABASE_APP_NAME)
    juju.ext.model.remove_application(
        DATABASE_APP_NAME,
        block_until_done=True,
        destroy_storage=True,
        timeout=15 * 60,
    )

    detaching = [
        storage for storage in juju.ext.model.list_storage() if storage["life"] == "detaching"
    ]
    assert not detaching, f"storage stuck detaching after removal: {detaching}"
