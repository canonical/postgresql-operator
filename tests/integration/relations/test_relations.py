#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import CHARM_BASE, METADATA
from ..new_relations.test_new_relations_1 import APPLICATION_APP_NAME, build_connection_string
from ..relations.helpers import get_legacy_db_connection_str

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
# MAILMAN3_CORE_APP_NAME = "mailman3-core"
DB_RELATION = "db"
DATABASE_RELATION = "database"
FIRST_DATABASE_RELATION = "database"
DATABASE_APP_NAME = "database-app"
DB_APP_NAME = "db-app"
APP_NAMES = [APP_NAME, DATABASE_APP_NAME, DB_APP_NAME]


@pytest.mark.abort_on_fail
async def test_deploy_charms(ops_test: OpsTest, charm):
    """Deploy both charms (application and database) to use in the tests."""
    # Deploy both charms (multiple units for each application to test that later they correctly
    # set data in the relation application databag using only the leader unit).
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=DATABASE_APP_NAME,
                num_units=1,
                base=CHARM_BASE,
                channel="latest/edge",
            ),
            ops_test.model.deploy(
                charm,
                application_name=APP_NAME,
                num_units=1,
                base=CHARM_BASE,
                config={
                    "profile": "testing",
                    "plugin_unaccent_enable": "True",
                    "plugin_pg_trgm_enable": "True",
                },
            ),
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=DB_APP_NAME,
                num_units=1,
                base=CHARM_BASE,
                channel="latest/edge",
            ),
        )

        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=3000)


async def test_legacy_endpoint_with_multiple_related_endpoints(ops_test: OpsTest):
    await ops_test.model.relate(f"{DB_APP_NAME}:{DB_RELATION}", f"{APP_NAME}:{DB_RELATION}")
    await ops_test.model.relate(APP_NAME, f"{DATABASE_APP_NAME}:{FIRST_DATABASE_RELATION}")

    app = ops_test.model.applications[APP_NAME]
    await ops_test.model.block_until(
        lambda: "blocked" in {unit.workload_status for unit in app.units},
        timeout=1500,
    )

    logger.info(" remove relation with  modern endpoints")
    await ops_test.model.applications[APP_NAME].remove_relation(
        f"{APP_NAME}:{DATABASE_RELATION}", f"{DATABASE_APP_NAME}:{FIRST_DATABASE_RELATION}"
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=1500,
            raise_on_error=False,
        )

    legacy_interface_connect = await get_legacy_db_connection_str(
        ops_test, DB_APP_NAME, DB_RELATION, remote_unit_name=f"{APP_NAME}/0"
    )
    logger.info(f" check connect to = {legacy_interface_connect}")
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(10)):
        with attempt, psycopg2.connect(legacy_interface_connect) as connection:
            assert connection.status == psycopg2.extensions.STATUS_READY

    logger.info(f" remove relation {DB_APP_NAME}:{DB_RELATION}")
    async with ops_test.fast_forward():
        await ops_test.model.applications[APP_NAME].remove_relation(
            f"{APP_NAME}:{DB_RELATION}", f"{DB_APP_NAME}:{DB_RELATION}"
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
        for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(10)):
            with attempt, pytest.raises(psycopg2.OperationalError):
                psycopg2.connect(legacy_interface_connect)


async def test_modern_endpoint_with_multiple_related_endpoints(ops_test: OpsTest):
    await ops_test.model.relate(f"{DB_APP_NAME}:{DB_RELATION}", f"{APP_NAME}:{DB_RELATION}")
    await ops_test.model.relate(APP_NAME, f"{DATABASE_APP_NAME}:{FIRST_DATABASE_RELATION}")

    app = ops_test.model.applications[APP_NAME]
    await ops_test.model.block_until(
        lambda: "blocked" in {unit.workload_status for unit in app.units},
        timeout=1500,
    )

    logger.info(" remove relation with legacy endpoints")
    await ops_test.model.applications[APP_NAME].remove_relation(
        f"{DB_APP_NAME}:{DB_RELATION}", f"{APP_NAME}:{DB_RELATION}"
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", timeout=3000, raise_on_error=False
        )

    modern_interface_connect = await build_connection_string(
        ops_test, DATABASE_APP_NAME, FIRST_DATABASE_RELATION
    )
    logger.info(f"check connect to = {modern_interface_connect}")
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(10)):
        with attempt, psycopg2.connect(modern_interface_connect) as connection:
            assert connection.status == psycopg2.extensions.STATUS_READY

    logger.info(f"remove relation {DATABASE_APP_NAME}:{FIRST_DATABASE_RELATION}")
    async with ops_test.fast_forward():
        await ops_test.model.applications[APP_NAME].remove_relation(
            f"{APP_NAME}:{DATABASE_RELATION}", f"{DATABASE_APP_NAME}:{FIRST_DATABASE_RELATION}"
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
        for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(10)):
            with attempt, pytest.raises(psycopg2.OperationalError):
                psycopg2.connect(modern_interface_connect)
