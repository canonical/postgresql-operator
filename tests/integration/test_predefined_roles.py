#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import jubilant
import psycopg2
import pytest as pytest
import yaml
from tenacity import Retrying, stop_after_delay, wait_fixed

from .helpers import (
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
    db_connect,
)
from .jubilant_helpers import (
    get_credentials,
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
TIMEOUT = 15 * 60


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
def test_deploy(juju: jubilant.Juju, charm, predefined_roles_combinations) -> None:
    """Deploy and relate the charms."""
    drop_databases = False
    reset_relation = False

    # Deploy the database charm if not already deployed.
    if DATABASE_APP_NAME not in juju.status().apps:
        logger.info("Deploying database charm")
        juju.deploy(
            charm,
            config={"profile": "testing"},
            num_units=2,
        )
    else:
        drop_databases = True

    for combination in predefined_roles_combinations:
        # Drop the database requested by each data integrator when restarting the test.
        suffix = (
            f"-{'-'.join(combination)}".replace("_", "-").lower()
            if "-".join(combination) != ""
            else ""
        )
        database_name = f"{REQUESTED_DATABASE_NAME}{suffix}"
        if drop_databases:
            logger.info(
                f"Dropping {database_name} database (and it's related users) from already deployed database charm"
            )
            connection = None
            try:
                primary = get_primary(juju, f"{DATABASE_APP_NAME}/0")
                host = get_unit_address(juju, primary)
                password = get_password()
                connection = db_connect(host, password)
                connection.autocommit = True
                with connection.cursor() as cursor:
                    cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}";')
                    cursor.execute(f'DROP ROLE IF EXISTS "{database_name}_admin";')
                    cursor.execute(f'DROP ROLE IF EXISTS "{database_name}_owner";')
            finally:
                if connection is not None:
                    connection.close()
            reset_relation = True

        # Deploy the data integrator charm for each combination of predefined roles.
        data_integrator_app_name = f"{DATA_INTEGRATOR_APP_NAME}{suffix}"
        extra_user_roles = ",".join(combination)
        if data_integrator_app_name not in juju.status().apps:
            logger.info(
                f"Deploying data integrator charm {'with extra user roles: ' + extra_user_roles.replace(',', ', ') if extra_user_roles else 'without extra user roles'}"
            )
            juju.deploy(
                DATA_INTEGRATOR_APP_NAME,
                app=data_integrator_app_name,
                config={"database-name": database_name, "extra-user-roles": extra_user_roles},
            )
        else:
            logger.info("Resetting extra user roles in already deployed data integrator charm")
            juju.config(
                app=data_integrator_app_name,
                values={
                    "database-name": database_name,
                    "extra-user-roles": extra_user_roles,
                },
            )
            reset_relation = True

        # Relate the data integrator charm to the database charm.
        existing_relations = relations(juju, DATABASE_APP_NAME, data_integrator_app_name)
        if reset_relation and existing_relations:
            logger.info("Removing existing relation between charms")
            juju.remove_relation(
                f"{data_integrator_app_name}:{RELATION_ENDPOINT}", DATABASE_APP_NAME
            )

            def all_blocked(status: jubilant.Status, app_name=data_integrator_app_name) -> bool:
                return jubilant.all_blocked(status, app_name)

            juju.wait(lambda status: all_blocked(status), timeout=TIMEOUT)
            logger.info("Adding relation between charms")
            for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(5)):
                with attempt:
                    juju.integrate(data_integrator_app_name, DATABASE_APP_NAME)
        if not existing_relations:
            logger.info("Adding relation between charms")
            juju.integrate(data_integrator_app_name, DATABASE_APP_NAME)

    juju.wait(lambda status: jubilant.all_active(status), timeout=TIMEOUT)


def test_connection(juju: jubilant.Juju, predefined_roles_combinations) -> None:
    """Check that the data integrator user can connect to the allowed databases."""
    primary = get_primary(juju, f"{DATABASE_APP_NAME}/0")
    host = get_unit_address(juju, primary)
    password = get_password()
    connection = None
    cursor = None
    try:
        connection = db_connect(host, password)
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("CREATE EXTENSION IF NOT EXISTS dblink;")
        cursor.execute(f'DROP DATABASE IF EXISTS "{OTHER_DATABASE_NAME}";')
        cursor.execute(f'CREATE DATABASE "{OTHER_DATABASE_NAME}";')
        cursor.execute("SELECT datname FROM pg_database;")
        databases = sorted(database[0] for database in cursor.fetchall())
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    data_integrator_apps = [
        app for app in juju.status().apps if app.startswith(DATA_INTEGRATOR_APP_NAME)
    ]
    for data_integrator_app_name in data_integrator_apps:
        credentials = get_credentials(juju, f"{data_integrator_app_name}/0")
        user = credentials["postgresql"]["username"]
        password = credentials["postgresql"]["password"]
        database = credentials["postgresql"]["database"]
        extra_user_roles = juju.config(app=data_integrator_app_name, app_config=True).get(
            "extra_user_roles"
        )
        primary = get_primary(juju, f"{DATABASE_APP_NAME}/0")
        host = get_unit_address(juju, primary)
        for database_to_test in databases:
            connection = None
            try:
                message_prefix = f"Checking that {user} user ({'with extra user roles: ' + extra_user_roles.replace(',', ', ') if extra_user_roles else 'without extra user roles'})"
                if database_to_test in [database, OTHER_DATABASE_NAME]:
                    logger.info(f"{message_prefix} can connect to {database_to_test} database")
                    connection = db_connect(
                        host, password, username=user, database=database_to_test
                    )
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_database();")
                        assert cursor.fetchone()[0] == database_to_test
                else:
                    logger.info(f"{message_prefix} can't connect to {database_to_test} database")
                    with pytest.raises(psycopg2.OperationalError) as exc_info:
                        db_connect(host, password, username=user, database=database_to_test)
                    assert "no pg_hba.conf entry" in str(exc_info.value)
            finally:
                if connection is not None:
                    connection.close()
