#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import psycopg2
import pytest
import requests
from psycopg2 import sql
import subprocess
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential, wait_fixed

from ..helpers import (
    CHARM_SERIES,
    get_unit_address,
    APPLICATION_NAME,
    get_primary,
)

from .helpers import (
    storage_id,
    is_storage_exists,
    create_db,
    check_db
)

TEST_DATABASE_RELATION_NAME = "test_database"
DUP_APPLICATION_NAME = "postgres-test-dup"

logger = logging.getLogger(__name__)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_removal(ops_test: OpsTest, charm: str):
    # Deploy the charm.
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
    host = get_unit_address(ops_test, primary_name)
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200

    storage_id_str = storage_id(ops_test, primary_name)

    # Check if storage exists after application deployed
    assert await is_storage_exists(ops_test, storage_id_str)

    await ops_test.model.remove_application(APPLICATION_NAME, block_until_done=True, destroy_storage=True)

    # Check if storage removed after application removal
    assert not await is_storage_exists(ops_test, storage_id_str)

    print(storage_id_str)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_force_removal(ops_test: OpsTest, charm: str):
    # Deploy the charm.
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
    host = get_unit_address(ops_test, primary_name)
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200

    storage_id_str = storage_id(ops_test, primary_name)

    # Check if storage exists after application deployed
    assert await is_storage_exists(ops_test, storage_id_str)

    await ops_test.model.remove_application(APPLICATION_NAME, block_until_done=True, force=True, no_wait=True)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_garbage_ignorance(ops_test: OpsTest, charm: str):
    # Deploy the charm.
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
    host = get_unit_address(ops_test, primary_name)
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200

    # Create test database to check there is no resouces conflicts
    await create_db(ops_test, APPLICATION_NAME, TEST_DATABASE_RELATION_NAME)

    # Deploy duplicaate charm
    await ops_test.model.deploy(
        charm,
        application_name=DUP_APPLICATION_NAME,
        num_units=1,
        series=CHARM_SERIES,
        config={"profile": "testing"},
    )

    await ops_test.model.wait_for_idle(apps=[DUP_APPLICATION_NAME], status="active", timeout=1500)
    assert ops_test.model.applications[DUP_APPLICATION_NAME].units[0].workload_status == "active"

    dup_primary_name = await get_primary(ops_test, ops_test.model.applications[DUP_APPLICATION_NAME].units[0].name)
    dup_host = get_unit_address(ops_test, dup_primary_name)
    dup_result = requests.get(f"http://{dup_host}:8008/health")
    assert dup_result.status_code == 200

    # Check that test database is not exists for duplicate application 
    assert not await check_db(ops_test, DUP_APPLICATION_NAME, TEST_DATABASE_RELATION_NAME)
