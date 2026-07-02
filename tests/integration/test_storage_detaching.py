#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest

from .adapters import JujuFixture
from .jubilant_helpers import DATABASE_APP_NAME

logger = logging.getLogger(__name__)

# rootfs (machine-scoped) storage is the issue's scenario. It matters:
# - Juju 3.6 masks the bug via a cleanup_storage shortcut that removes still-Dying
#   storage, so this only reproduces on Juju 4.0 (see tests/spread/.../task.yaml).
# - On 4.0 the detachable ``lxd`` pool lets Juju destroy the container before
#   detaching, so only ``rootfs`` reproduces the stuck unmount.
ROOTFS_STORAGE = dict.fromkeys(("archive", "data", "logs", "temp"), "rootfs")


@pytest.mark.abort_on_fail
def test_storage_released_on_removal(juju: JujuFixture, charm):
    """Graceful removal must release the storage so teardown completes.

    Regression test for canonical/postgresql-operator#1550: without stopping the
    workload in the ``storage-detaching`` hook the charmed-postgresql snap keeps the
    storage mounts busy, so Juju's unmount fails ("target is busy") and the unit,
    storage and machine removal hangs forever.

    Pinned to Juju 4.0 with rootfs storage by its spread task. ``force=True`` is
    required because the charm still declares ``assumes: juju < 4``.
    """
    juju.ext.model.deploy(
        charm,
        num_units=1,
        config={"profile": "testing"},
        storage=ROOTFS_STORAGE,
        force=True,
    )
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

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
