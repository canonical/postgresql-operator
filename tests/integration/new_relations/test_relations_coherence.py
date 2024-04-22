#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import CHARM_SERIES, DATABASE_APP_NAME
from .helpers import check_relation_data_existence

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
APP_NAMES = [DATABASE_APP_NAME, APPLICATION_APP_NAME]
FIRST_DATABASE_RELATION_NAME = "first-database"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_charms(ops_test: OpsTest, charm):
    """Deploy both charms (application and database) to use in the tests."""
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=APPLICATION_APP_NAME,
                num_units=2,
                series=CHARM_SERIES,
                channel="edge",
            ),
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
                config={"profile": "testing"},
            ),
        )

        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", timeout=3000)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest, charm):
    """Test that check relation data."""
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=APPLICATION_APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
                channel="edge",
            ),
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
                config={"profile": "testing"},
            ),
        )

        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", timeout=3000)

        # Relate the charms and wait for them exchanging some connection data.
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        assert await check_relation_data_existence(
            ops_test,
            APPLICATION_APP_NAME,
            FIRST_DATABASE_RELATION_NAME,
            "read-only-endpoints",
            exists=False,
        )

        # Remove relation, relate 2nd time and check relation data
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database",
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}",
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")
        assert await check_relation_data_existence(
            ops_test,
            APPLICATION_APP_NAME,
            FIRST_DATABASE_RELATION_NAME,
            "read-only-endpoints",
            exists=False,
        )

        # Repeat re-relation using relation options and check relation data
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database",
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}",
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        await ops_test.model.applications[APPLICATION_APP_NAME].set_config({
            # "database-name": APPLICATION_APP_NAME.replace("-", "_"),
            "legacy_roles": "true",
        })
        await ops_test.model.wait_for_idle(apps=[APPLICATION_APP_NAME], status="blocked")
        await ops_test.model.add_relation(APPLICATION_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        assert await check_relation_data_existence(
            ops_test,
            APPLICATION_APP_NAME,
            FIRST_DATABASE_RELATION_NAME,
            "read-only-endpoints",
            exists=True,
        )
