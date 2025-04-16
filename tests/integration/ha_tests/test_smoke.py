#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
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
    get_any_deatached_storage,
    is_postgresql_ready,
    is_storage_exists,
    storage_id,
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
            storage={"pgdata": {"pool": "lxd-btrfs", "size": 8046}},
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
        storage_id_str = storage_id(ops_test, primary_name)

        # Check if storage exists after application deployed
        logger.info("verifying if storage exists")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id_str)

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
        logger.info("werifing is storage exists")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id_str)


@pytest.mark.abort_on_fail
async def test_charm_garbage_ignorance(ops_test: OpsTest, charm: str):
    """Test charm deploy in dirty environment with garbage storage."""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        logger.info("add unit with attached storage")
        await add_unit_with_storage(ops_test, DATABASE_APP_NAME, garbage_storage)

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        logger.info("getting storage id")
        storage_id_str = storage_id(ops_test, primary_name)

        assert storage_id_str == garbage_storage

        # Check if storage exists after application deployed
        logger.info("werifing is storage exists")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id_str)

        # Check that test database exists for new unit
        logger.info("checking db")
        assert await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME)

        logger.info("removing charm")
        await ops_test.model.destroy_unit(primary_name)


@pytest.mark.abort_on_fail
async def test_app_resources_conflicts_v3(ops_test: OpsTest, charm: str):
    """Test application deploy in dirty environment with garbage storage from another application."""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        logger.info("deploying duplicate application with attached storage")
        await ops_test.model.deploy(
            charm,
            application_name=DUP_DATABASE_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
            attach_storage=[tag.storage(garbage_storage)],
            config={"profile": "testing"},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        logger.info("waiting for duplicate application to be blocked")
        try:
            await ops_test.model.wait_for_idle(
                apps=[DUP_DATABASE_APP_NAME], timeout=1000, status="blocked"
            )
        except asyncio.TimeoutError:
            logger.info("Application is not in blocked state. Checking logs...")

        # Since application have postgresql db in storage from external application it should not be able to connect due to new password
        logger.info("checking operator password auth")
        assert not await check_password_auth(
            ops_test, ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].name
        )
