#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .helpers import (
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    relations,
)

logger = logging.getLogger(__name__)

REQUESTED_DATABASE_NAME = "requested-database"
OTHER_DATABASE_NAME = "other-database"
RELATION_ENDPOINT = "postgresql"
CONFIGURATION_FILE_PATH = "tests/integration/predefined_roles.yaml"


class PredefinedRole(yaml.YAMLObject):
    yaml_tag = "!PredefinedRole"

    def __init__(self, name, database_owner, in_roles, permissions):
        self.name = name
        self.database_owner = database_owner
        self.in_roles = in_roles
        self.permissions = permissions


@pytest.fixture(scope="module")
def predefined_roles() -> str:
    with open(CONFIGURATION_FILE_PATH) as file:
        data = yaml.load(file, Loader=yaml.Loader)
        return data["extra-user-roles"]


@pytest.fixture(scope="module")
def predefined_roles_combinations() -> str:
    with open(CONFIGURATION_FILE_PATH) as file:
        data = yaml.load(file, Loader=yaml.Loader)
        return data["allowed-combinations"]


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm, predefined_roles_combinations) -> None:
    """Deploy and relate the charms."""
    drop_databases = False
    reset_relation = False

    # Deploy the database charm if not already deployed.
    if DATABASE_APP_NAME not in ops_test.model.applications:
        logger.info("Deploying database charm")
        await ops_test.model.deploy(charm, config={"profile": "testing"}, num_units=2)
    else:
        drop_databases = True
    applications = [DATABASE_APP_NAME]

    for index, combination in enumerate(predefined_roles_combinations):
        # Drop the database requested by each data integrator when restarting the test.
        database_name = f"{REQUESTED_DATABASE_NAME}-{index}"
        if drop_databases:
            logger.info(f"Dropping {database_name} database from already deployed database charm")
            primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
            connection = None
            try:
                host = get_unit_address(ops_test, primary)
                password = await get_password(ops_test, database_app_name=DATABASE_APP_NAME)
                connection = db_connect(host, password)
                connection.autocommit = True
                with connection.cursor() as cursor:
                    cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}";')
            finally:
                if connection is not None:
                    connection.close()
            reset_relation = True

        # Deploy the data integrator charm for each combination of predefined roles.
        data_integrator_app_name = f"{DATA_INTEGRATOR_APP_NAME}{index}"
        extra_user_roles = ",".join(combination)
        if data_integrator_app_name not in ops_test.model.applications:
            logger.info(
                f"Deploying data integrator charm {'with extra user roles: ' + extra_user_roles.replace(',', ', ') if extra_user_roles else 'without extra user roles'}"
            )
            await ops_test.model.deploy(
                DATA_INTEGRATOR_APP_NAME,
                application_name=data_integrator_app_name,
                config={"database-name": database_name, "extra-user-roles": extra_user_roles},
            )
        else:
            logger.info("Resetting extra user roles in already deployed data integrator charm")
            await ops_test.model.applications[data_integrator_app_name].set_config({
                "database-name": database_name,
                "extra-user-roles": extra_user_roles,
            })
            reset_relation = True

        # Relate the data integrator charm to the database charm.
        existing_relations = relations(ops_test, DATABASE_APP_NAME, data_integrator_app_name)
        if reset_relation and existing_relations:
            logger.info("Removing existing relation between charms")
            await ops_test.model.applications[data_integrator_app_name].remove_relation(
                f"{data_integrator_app_name}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
            )
            async with ops_test.fast_forward():
                await ops_test.model.wait_for_idle(
                    apps=[data_integrator_app_name], status="blocked"
                )
            logger.info("Adding relation between charms")
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    await ops_test.model.relate(data_integrator_app_name, DATABASE_APP_NAME)
        if not existing_relations:
            logger.info("Adding relation between charms")
            await ops_test.model.relate(data_integrator_app_name, DATABASE_APP_NAME)

        applications.append(data_integrator_app_name)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=applications, status="active")
