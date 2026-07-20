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
def test_storage_released_on_removal(juju: JujuFixture, charm):
    """Graceful removal must release the storage so teardown completes.

    Regression test for canonical/postgresql-operator#1550: without stopping the
    workload in the ``storage-detaching`` hook the snap keeps the storage mounts
    busy, so Juju's unmount fails ("target is busy") and teardown hangs.
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
