#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from juju import tag
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
)
from .helpers import (
    add_unit_with_storage,
    check_db,
    check_password_auth,
    create_db,
    get_detached_storages,
    get_storage_ids,
    is_postgresql_ready,
    is_storage_exists,
)

TEST_DATABASE_NAME = "test_database"
DUP_DATABASE_APP_NAME = "postgres-test-dup"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_app_force_removal(ops_test: OpsTest, charm: str):
    """Remove unit with force while storage is alive."""
    async with ops_test.fast_forward():
        # Deploy the charm.
        logger.info("deploying charm")
        await ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
            storage={
                "archive": {"pool": "lxd-btrfs", "size": 2048},
                "data": {"pool": "lxd-btrfs", "size": 2048},
                "logs": {"pool": "lxd-btrfs", "size": 2048},
                "temp": {"pool": "lxd-btrfs", "size": 2048},
            },
            config={"profile": "testing"},
        )

        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        logger.info("getting storage id")
        storage_ids = get_storage_ids(ops_test, primary_name)

        # Check if storage exists after application deployed
        logger.info("verifying that storage exists")
        for storage_id in storage_ids:
            for attempt in Retrying(
                stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True
            ):
                with attempt:
                    assert await is_storage_exists(ops_test, storage_id)

        # Create test database to check there is no resources conflicts
        logger.info("creating db")
        await create_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME)

        # Check that test database is not exists for new unit
        logger.info("checking db")
        assert await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME)

        # Destroy charm
        logger.info("force removing charm")
        await ops_test.model.destroy_unit(
            primary_name, force=True, destroy_storage=False, max_wait=1500
        )

        # Storage should remain
        logger.info("verifying that storage exists")
        for storage_id in storage_ids:
            for attempt in Retrying(
                stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True
            ):
                with attempt:
                    assert await is_storage_exists(ops_test, storage_id)


@pytest.mark.abort_on_fail
async def test_charm_garbage_ignorance(ops_test: OpsTest, charm: str):
    """Test charm deploy in dirty environment with garbage storage."""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storages = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storages = await get_detached_storages(ops_test)
                assert len(garbage_storages) == 4
                logger.info(f"Collected storages: {garbage_storages}")

        logger.info("add unit with attached storage")
        await add_unit_with_storage(ops_test, DATABASE_APP_NAME, garbage_storages)

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        logger.info("getting storage id")
        storage_ids = get_storage_ids(ops_test, primary_name)

        for storage_id in storage_ids:
            assert storage_id in garbage_storages

        # Check if storage exists after application deployed
        logger.info("verifying is storage exists")
        for storage_id in storage_ids:
            for attempt in Retrying(
                stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True
            ):
                with attempt:
                    assert await is_storage_exists(ops_test, storage_id)

        # Check that test database exists for new unit
        logger.info("checking db")
        assert await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME)

        logger.info("removing charm")
        await ops_test.model.destroy_unit(primary_name)


@pytest.mark.abort_on_fail
@pytest.mark.skip(reason="Unstable")
async def test_app_resources_conflicts_v3(ops_test: OpsTest, charm: str):
    """Test application deploy in dirty environment with garbage storage from another application."""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storages = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storages = await get_detached_storages(ops_test)
                assert len(garbage_storages) == 4
                logger.info(f"Collected storages: {garbage_storages}")

        logger.info("deploying duplicate application with attached storage")
        await ops_test.model.deploy(
            charm,
            application_name=DUP_DATABASE_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
            attach_storage=[tag.storage(storage) for storage in garbage_storages],
            config={"profile": "testing"},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        logger.info("waiting for duplicate application to be waiting")
        try:
            await ops_test.model.wait_for_idle(
                apps=[DUP_DATABASE_APP_NAME], timeout=60, idle_period=30, status="waiting"
            )
        except TimeoutError:
            logger.info("Application is not in waiting state. Checking logs...")

        for attempt in Retrying(stop=stop_after_delay(60 * 10), wait=wait_fixed(3), reraise=True):
            with attempt:
                # Since application have postgresql db in storage from external application it should not be able to connect due to new password
                assert not await check_password_auth(
                    ops_test, ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].name
                )
