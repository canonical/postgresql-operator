#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

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

    doomed = [unit.name for unit in juju.ext.model.applications[DATABASE_APP_NAME].units][-2:]
    logger.info("Removing %s at once; the two survivors must keep quorum", doomed)
    for unit in doomed:
        juju.ext.model.destroy_unit(unit, destroy_storage=True)
    juju.ext.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], status="active", wait_for_exact_units=2, timeout=15 * 60
    )

    detaching = [
        storage for storage in juju.ext.model.list_storage() if storage["life"] == "detaching"
    ]
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
