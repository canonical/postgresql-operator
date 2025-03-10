#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    execute_query_on_unit,
    get_password,
    get_unit_address,
)

logger = logging.getLogger(__name__)

GLAUTH_PSQL_APP_NAME = "postgresql-k8s"
GLAUTH_CERT_APP_NAME = "self-signed-certificates"
GLAUTH_APP_NAME = "glauth-k8s"


@pytest.mark.abort_on_fail
@markers.juju3
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three units of PostgreSQL."""
    await build_and_deploy(ops_test, charm, num_units=1, wait_for_idle=True)


@pytest.mark.abort_on_fail
@markers.juju3
async def test_glauth_integration(ops_test: OpsTest):
    glauth_psql_app_name = f"glauth-{GLAUTH_PSQL_APP_NAME}"
    glauth_cert_app_name = f"glauth-{GLAUTH_CERT_APP_NAME}"

    # Deploy GLAuth charm
    await asyncio.gather(
        ops_test.model.deploy(
            GLAUTH_PSQL_APP_NAME,
            application_name=glauth_psql_app_name,
            channel="14/stable",
            trust=True,
        ),
        ops_test.model.deploy(
            GLAUTH_CERT_APP_NAME,
            application_name=glauth_cert_app_name,
            channel="latest/stable",
            trust=False,
        ),
        ops_test.model.deploy(
            GLAUTH_APP_NAME,
            application_name=GLAUTH_APP_NAME,
            channel="latest/edge",
            trust=True,
        ),
    )

    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[glauth_psql_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[glauth_cert_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[GLAUTH_APP_NAME], status="blocked"),
        )

        # Add both relations to GLAuth (PostgreSQL and self-signed-certificates)
        logger.info("Adding relations to GLAuth")
        await asyncio.gather(
            ops_test.model.add_relation(GLAUTH_APP_NAME, glauth_psql_app_name),
            ops_test.model.add_relation(GLAUTH_APP_NAME, glauth_cert_app_name),
        )
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[glauth_psql_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[glauth_cert_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[GLAUTH_APP_NAME], status="active"),
        )

        # Add relation to PostgreSQL
        logger.info("Adding relation to PostgreSQL")
        await ops_test.model.add_relation(
            f"{GLAUTH_APP_NAME}:ldap",
            f"{DATABASE_APP_NAME}:ldap",
        )
        await ops_test.model.add_relation(
            f"{GLAUTH_APP_NAME}:send-ca-cert",
            f"{DATABASE_APP_NAME}:receive-ca-cert",
        )

        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

        database_units = ops_test.model.applications[DATABASE_APP_NAME].units
        address = get_unit_address(ops_test, database_units[0].name)
        password = await get_password(ops_test, database_units[0].name)

        # Validate the 'operator' user can still access the instance
        await execute_query_on_unit(address, password, "SELECT VERSION();")
