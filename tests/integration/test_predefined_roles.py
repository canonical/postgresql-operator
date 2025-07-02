#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import jubilant
import psycopg2
import pytest as pytest
from psycopg2.sql import Identifier, SQL

from .helpers import (
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
    check_connected_user,
    db_connect,
)
from .jubilant_helpers import (
    get_credentials,
    get_password,
    get_primary,
    get_unit_address,
    relations,
    roles_attributes,
)

logger = logging.getLogger(__name__)

OTHER_DATABASE_NAME = "other-database"
REQUESTED_DATABASE_NAME = "requested-database"
RELATION_ENDPOINT = "postgresql"
ROLE_DATABASES_OWNER = "charmed_databases_owner"
TIMEOUT = 15 * 60


@pytest.mark.abort_on_fail
def test_deploy(juju: jubilant.Juju, charm, predefined_roles_combinations) -> None:
    """Deploy and relate the charms."""
    # Deploy the database charm if not already deployed.
    if DATABASE_APP_NAME not in juju.status().apps:
        logger.info("Deploying database charm")
        juju.deploy(
            charm,
            config={"profile": "testing"},
            num_units=1,
        )

    for combination in predefined_roles_combinations:
        # Drop the database requested by each data integrator when restarting the test.
        suffix = (
            f"-{'-'.join(combination)}".replace("_", "-").lower()
            if "-".join(combination) != ""
            else ""
        )
        database_name = f"{REQUESTED_DATABASE_NAME}{suffix}"

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

        # Relate the data integrator charm to the database charm.
        existing_relations = relations(juju, DATABASE_APP_NAME, data_integrator_app_name)
        if not existing_relations:
            logger.info("Adding relation between charms")
            juju.integrate(data_integrator_app_name, DATABASE_APP_NAME)

    juju.wait(lambda status: jubilant.all_active(status), timeout=TIMEOUT)


def test_operations(juju: jubilant.Juju, predefined_roles) -> None:
    """Check that the data integrator user can perform the expected operations in each database."""
    primary = get_primary(juju, f"{DATABASE_APP_NAME}/0")
    host = get_unit_address(juju, primary)
    operator_password = get_password()
    connection = None
    cursor = None
    try:
        connection = db_connect(host, operator_password)
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("CREATE EXTENSION IF NOT EXISTS dblink;")
        cursor.execute(f'DROP DATABASE IF EXISTS "{OTHER_DATABASE_NAME}";')
        cursor.execute(f'CREATE DATABASE "{OTHER_DATABASE_NAME}";')
        cursor.execute("SELECT datname FROM pg_database;")
        databases = []
        for database in sorted(database[0] for database in cursor.fetchall()):
            if database.startswith(f"{OTHER_DATABASE_NAME}-"):
                logger.info(f"Dropping database {database} created by the test")
                cursor.execute(SQL("DROP DATABASE {};").format(Identifier(database)))
            else:
                databases.append(database)
                if database not in ["postgres", "template0", "template1"]:
                    sub_connection = None
                    try:
                        sub_connection = db_connect(host, operator_password, database=database)
                        sub_connection.autocommit = True
                        with sub_connection.cursor() as sub_cursor:
                            sub_cursor.execute("SELECT schema_name FROM information_schema.schemata;")
                            for schema in sub_cursor.fetchall():
                                schema_name = schema[0]
                                if schema_name.startswith("relation-") and schema_name.endswith("_schema"):
                                    logger.info(f"Dropping schema {schema_name} created by the test")
                                    sub_cursor.execute(SQL("DROP SCHEMA {} CASCADE;").format(Identifier(schema_name)))
                    finally:
                        if sub_connection is not None:
                            sub_connection.close()
        logger.info(f"Databases to test: {databases}")
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
        config = juju.config(app=data_integrator_app_name)
        logger.info(f"Config for {data_integrator_app_name}: {config}")
        extra_user_roles = config.get(
            "extra-user-roles", ""
        )
        logger.info(
            f"User is {user}, database is {database}, extra user roles are '{extra_user_roles}'")
        attributes = roles_attributes(predefined_roles, extra_user_roles)
        logger.info(f"Attributes for user {user}: '{attributes}'")
        message_prefix = f"Checking that {user} user ({'with extra user roles: ' + extra_user_roles.replace(',', ', ') if extra_user_roles else 'without extra user roles'})"
        already_checked_database_creation = False
        for database_to_test in databases:
            connection = None
            cursor = None
            operator_connection = None
            operator_cursor = None
            try:
                connect_permission = attributes["permissions"]["connect"]
                if (connect_permission == "*" and (("CREATEDB" in extra_user_roles and database_to_test not in ["postgres", "template0"]) or database_to_test not in ["postgres", "template0", "template1"])) or (connect_permission == True and database_to_test == database) or database_to_test == OTHER_DATABASE_NAME:
                    logger.info(f"{message_prefix} can connect to {database_to_test} database")
                    connection = db_connect(
                        host, password, username=user, database=database_to_test
                    )
                    connection.autocommit = True
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_database();")
                        assert cursor.fetchone()[0] == database_to_test
                else:
                    logger.info(f"{message_prefix} can't connect to {database_to_test} database")
                    with pytest.raises(psycopg2.OperationalError):
                        db_connect(host, password, username=user, database=database_to_test)

                if connection is not None:
                    auto_escalate_to_database_owner = attributes["auto-escalate-to-database-owner"]
                    database_owner_user = f"charmed_{database_to_test}_owner"
                    with connection, connection.cursor() as cursor:
                        if "CREATEDB" in extra_user_roles:
                            logger.info(f"{message_prefix} auto escalates to {ROLE_DATABASES_OWNER}")
                            check_connected_user(cursor, user, ROLE_DATABASES_OWNER)
                        elif (auto_escalate_to_database_owner == "*" and database_to_test != OTHER_DATABASE_NAME) or (auto_escalate_to_database_owner == True and database_to_test == database):
                            logger.info(f"{message_prefix} auto escalates to {database_owner_user}")
                            check_connected_user(cursor, user, database_owner_user)
                        else:
                            logger.info(f"{message_prefix} doesn't auto escalate to {database_owner_user}")
                            check_connected_user(cursor, user, user)

                    escalate_to_database_owner_permission = attributes["permissions"]["escalate-to-database-owner"]

                    schema_name = f"{user}_schema"
                    statements ={
                        "schema": SQL("CREATE SCHEMA {};").format(Identifier(schema_name)),
                        "create": SQL("CREATE TABLE {}.test_table(value TEXT);").format(Identifier(schema_name)),
                        "create-in-public-schema": SQL("CREATE TABLE test_table_{}(value TEXT);").format(Identifier(user)),
                        "write": SQL("INSERT INTO {}.test_table VALUES ('test');").format(Identifier(schema_name)),
                        "write-in-public-schema": SQL("INSERT INTO test_table_{} VALUES ('test');").format(Identifier(user)),
                        "read": SQL("SELECT * FROM {}.test_table;").format(Identifier(schema_name)),
                        "read-in-public-schema": SQL("SELECT * FROM test_table_{};").format(Identifier(user)),
                    }

                    # Test objects creation.
                    create_objects_permission = attributes["permissions"]["create-objects"]
                    if (create_objects_permission == "*" and database_to_test not in [OTHER_DATABASE_NAME, "template1"]) or (create_objects_permission == "*" and database_to_test == database) or (escalate_to_database_owner_permission == "*" and database_to_test not in [OTHER_DATABASE_NAME, "template1"]) or (escalate_to_database_owner_permission == True and database_to_test == database):
                        logger.info(f"{message_prefix} can create schemas")
                        with connection.cursor() as cursor:
                            if "CREATEDB" in extra_user_roles or (escalate_to_database_owner_permission and not auto_escalate_to_database_owner):
                                cursor.execute(SQL("SET ROLE {};").format(Identifier(database_owner_user)))
                            cursor.execute(statements["schema"])
                            cursor.execute(statements["create"])
                    else:
                        logger.info(f"{message_prefix} can't create schemas")
                        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                            with connection.cursor() as cursor:
                                cursor.execute(statements["schema"])

                        operator_connection = db_connect(host, operator_password, database=database_to_test)
                        operator_connection.autocommit = True
                        operator_cursor = operator_connection.cursor()
                        operator_cursor.execute(statements["schema"])
                        operator_cursor.execute(statements["create"])
                        operator_cursor.close()
                        operator_cursor = None
                        operator_connection.close()
                        operator_connection = None

                    # Test write permissions.
                    write_data_permission = attributes["permissions"]["write-data"]
                    if write_data_permission == "*" or (write_data_permission == True and database_to_test == database) or escalate_to_database_owner_permission == "*" or (escalate_to_database_owner_permission == True and database_to_test == database):
                        logger.info(f"{message_prefix} can write to tables in {schema_name} schema")
                        with connection.cursor() as cursor:
                            if escalate_to_database_owner_permission and not auto_escalate_to_database_owner:
                                cursor.execute(SQL("SET ROLE {};").format(Identifier(database_owner_user)))
                            cursor.execute(statements["write"])
                    else:
                        logger.info(f"{message_prefix} can't write to tables in {schema_name} schema")
                        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                            with connection.cursor() as cursor:
                                cursor.execute(statements["write"])

                    # Test read permissions.
                    read_data_permission = attributes["permissions"]["read-data"]
                    if read_data_permission == "*" or (read_data_permission == True and database_to_test == database) or escalate_to_database_owner_permission == "*" or (escalate_to_database_owner_permission == True and database_to_test == database):
                        logger.info(f"{message_prefix} can read from tables in {schema_name} schema")
                        with connection.cursor() as cursor:
                            if escalate_to_database_owner_permission and not auto_escalate_to_database_owner:
                                cursor.execute(SQL("SET ROLE {};").format(Identifier(database_owner_user)))
                            cursor.execute(statements["read"])
                    else:
                        logger.info(f"{message_prefix} can't read from tables in {schema_name} schema")
                        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                            with connection.cursor() as cursor:
                                cursor.execute(statements["read"])

                    # Test permission to call the set_up_predefined_catalog_roles function.
                    statement = "SELECT set_up_predefined_catalog_roles();"
                    if attributes["permissions"]["set-up-predefined-catalog-roles"]:
                        logger.info(f"{message_prefix} can call the set-up-predefined-catalog-roles function")
                        with connection.cursor() as cursor:
                            if escalate_to_database_owner_permission and not auto_escalate_to_database_owner:
                                cursor.execute(SQL("SET ROLE {};").format(Identifier(database_owner_user)))
                            cursor.execute(statement)
                    else:
                        logger.info(f"{message_prefix} can't call the set-up-predefined-catalog-roles function")
                        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                            with connection.cursor() as cursor:
                                cursor.execute(statement)

                    # Test database creation only once (otherwise, the test code will try to create
                    # an already exiting database the second time it reached this point).
                    if not already_checked_database_creation:
                        cursor = connection.cursor()
                        statement = SQL("CREATE DATABASE {};").format(Identifier(f"{OTHER_DATABASE_NAME}-{user}"))
                        if attributes["permissions"]["create-databases"]:
                            logger.info(f"{message_prefix} can create databases")
                            cursor.execute(statement)
                        else:
                            logger.info(f"{message_prefix} can't create databases")
                            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                                cursor.execute(statement)
                        cursor.close()
                        cursor = None
                        already_checked_database_creation = True
                    connection.close()
                    connection = None
            finally:
                if cursor is not None:
                    cursor.close()
                if connection is not None:
                    connection.close()
                if operator_cursor is not None:
                    operator_cursor.close()
                if operator_connection is not None:
                    operator_connection.close()
