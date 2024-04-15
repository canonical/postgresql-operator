#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from juju import tag
from asyncio import TimeoutError
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    CHARM_SERIES,
    APPLICATION_NAME,
    get_primary,
)

from .helpers import (
    storage_id,
    get_any_deatached_storage,
    is_postgresql_ready,
    is_storage_exists,
    create_db,
    check_db,
    check_password_auth,
)

TEST_GARBADGE_STORAGE_NAME = "test_pgdata"
TEST_DATABASE_RELATION_NAME = "test_database"
DUP_APPLICATION_NAME = "postgres-test-dup"

logger = logging.getLogger(__name__)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_removal(ops_test: OpsTest, charm: str):
    """Test all recoureces is removed after application removal"""
    # Deploy the charm.
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            application_name=APPLICATION_NAME,
            num_units=1,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        await ops_test.model.wait_for_idle(apps=[APPLICATION_NAME], status="active", timeout=1500)
        assert ops_test.model.applications[APPLICATION_NAME].units[0].workload_status == "active"

        primary_name = await get_primary(ops_test, ops_test.model.applications[APPLICATION_NAME].units[0].name)
        assert await is_postgresql_ready(ops_test, primary_name)

        storage_id_str = storage_id(ops_test, primary_name)

        # Check if storage exists after application deployed
        assert await is_storage_exists(ops_test, storage_id_str)

        await ops_test.model.remove_application(APPLICATION_NAME, block_until_done=True, destroy_storage=True)

        # Check if storage removed after application removal
        assert not await is_storage_exists(ops_test, storage_id_str)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_force_removal(ops_test: OpsTest, charm: str):
    """Remove unit with force while storage is alive"""
    async with ops_test.fast_forward():
        # Deploy the charm.
        await ops_test.model.deploy(
            charm,
            application_name=APPLICATION_NAME,
            num_units=1,
            series=CHARM_SERIES,
            storage={"pgdata": {"pool": "lxd-btrfs", "size": 8046}},
            config={"profile": "testing"},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        await ops_test.model.wait_for_idle(apps=[APPLICATION_NAME], status="active", timeout=1500)
        assert ops_test.model.applications[APPLICATION_NAME].units[0].workload_status == "active"

        primary_name = await get_primary(ops_test, ops_test.model.applications[APPLICATION_NAME].units[0].name)
        assert await is_postgresql_ready(ops_test, primary_name)

        storage_id_str = storage_id(ops_test, primary_name)

        # Check if storage exists after application deployed
        assert await is_storage_exists(ops_test, storage_id_str)

        # Create test database to check there is no resouces conflicts
        await create_db(ops_test, APPLICATION_NAME, TEST_DATABASE_RELATION_NAME)

        # Destroy charm
        await ops_test.model.destroy_unit(primary_name, force=True, destroy_storage=False, max_wait=1500)

        # Storage should remain
        assert await is_storage_exists(ops_test, storage_id_str)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_charm_garbage_ignorance(ops_test: OpsTest, charm: str):
    """Test charm deploy in dirty enviroment with garbadge storage"""
    async with ops_test.fast_forward():
        garbadge_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3)):
            with attempt:
                garbadge_storage = await get_any_deatached_storage(ops_test)
                assert garbadge_storage is not None

        assert garbadge_storage is not None

        await ops_test.model.applications[APPLICATION_NAME].add_unit(1, attach_storage=[tag.storage(garbadge_storage)])

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        await ops_test.model.wait_for_idle(apps=[APPLICATION_NAME], status="active", timeout=1500)
        assert ops_test.model.applications[APPLICATION_NAME].units[0].workload_status == "active"

        primary_name = await get_primary(ops_test, ops_test.model.applications[APPLICATION_NAME].units[0].name)
        assert await is_postgresql_ready(ops_test, primary_name)

        storage_id_str = storage_id(ops_test, primary_name)

        # Check if storage exists after application deployed
        assert await is_storage_exists(ops_test, storage_id_str)

        # Check that test database is not exists for duplicate application 
        assert not await check_db(ops_test, APPLICATION_NAME, TEST_DATABASE_RELATION_NAME)

        await ops_test.model.destroy_unit(primary_name, destroy_storage=False, max_wait=1500)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_recoures_conflicts(ops_test: OpsTest, charm: str):
    """Test application deploy in dirty enviroment with garbadge storage from another application"""
    async with ops_test.fast_forward():
        garbadge_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3)):
            with attempt:
                garbadge_storage = await get_any_deatached_storage(ops_test)
                assert garbadge_storage is not None

        assert garbadge_storage is not None

        # Deploy duplicaate charm
        await ops_test.model.deploy(
            charm,
            application_name=DUP_APPLICATION_NAME,
            num_units=1,
            series=CHARM_SERIES,
            config={"profile": "testing"},
            attach_storage=[tag.storage(garbadge_storage)]
        )

        try:
            await ops_test.model.wait_for_idle(apps=[DUP_APPLICATION_NAME], timeout=500, status="blocked")
        except (TimeoutError) as e:
            logger.info(f"Application is not in blocked state. Checking logs...")

        # Since application have postgresql db in storage from external application it should not be able to connect due to new password
        assert not await check_password_auth(ops_test, ops_test.model.applications[DUP_APPLICATION_NAME].units[0].name)
