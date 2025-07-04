#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import jubilant
import psycopg2
import pytest as pytest
from psycopg2.sql import (
    SQL,
    Identifier,
)

from .helpers import (
    DATA_INTEGRATOR_APP_NAME,
    DATABASE_APP_NAME,
    check_connected_user,
    db_connect,
)
from .jubilant_helpers import (
    RoleAttributeValue,
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
ROLE_DBA = "charmed_dba"
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

    combinations = [*predefined_roles_combinations, (ROLE_DBA,)]
    for combination in combinations:
        # Define an application name suffix and a database name based on the combination
        # of predefined roles.
        suffix = (
            f"-{'-'.join(combination)}".replace("_", "-").lower()
            if "-".join(combination) != ""
            else ""
        )
        database_name = f"{REQUESTED_DATABASE_NAME}{suffix}"

        # Deploy the data integrator charm for each combination of predefined roles.
        data_integrator_app_name = f"{DATA_INTEGRATOR_APP_NAME}{suffix}"
        extra_user_roles = "" if combination[0] == ROLE_DBA else ",".join(combination)
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


def test_operations(juju: jubilant.Juju, predefined_roles) -> None:  # noqa: C901
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
                if database != "template0":
                    sub_connection = None
                    try:
                        sub_connection = db_connect(host, operator_password, database=database)
                        sub_connection.autocommit = True
                        with sub_connection.cursor() as sub_cursor:
                            sub_cursor.execute(
                                "SELECT schema_name FROM information_schema.schemata;"
                            )
                            for schema in sub_cursor.fetchall():
                                schema_name = schema[0]
                                if schema_name.startswith("relation-") and schema_name.endswith(
                                    "_schema"
                                ):
                                    logger.info(
                                        f"Dropping schema {schema_name} created by the test"
                                    )
                                    sub_cursor.execute(
                                        SQL("DROP SCHEMA {} CASCADE;").format(
                                            Identifier(schema_name)
                                        )
                                    )
                            sub_cursor.execute(
                                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
                            )
                            for table in sub_cursor.fetchall():
                                table_name = table[0]
                                if table_name.startswith("test_table_"):
                                    logger.info(f"Dropping table {table_name} created by the test")
                                    sub_cursor.execute(
                                        SQL("DROP TABLE public.{} CASCADE;").format(Identifier(table_name))
                                    )
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
        if data_integrator_app_name.endswith(ROLE_DBA.replace("_", "-")):
            connection = None
            try:
                with db_connect(host, operator_password) as connection:
                    connection.autocommit = True
                    with connection.cursor() as cursor:
                        logger.info(
                            f"Granting {ROLE_DBA} role to {user} user to correctly check that role permissions"
                        )
                        cursor.execute(
                            SQL("GRANT {} TO {};").format(Identifier(ROLE_DBA), Identifier(user))
                        )
                        cursor.execute(
                            SQL("REVOKE {} FROM {};").format(
                                Identifier(f"charmed_{database}_dml"), Identifier(user)
                            )
                        )
                        cursor.execute(
                            SQL("REVOKE {} FROM {};").format(
                                Identifier(f"charmed_{database}_admin"), Identifier(user)
                            )
                        )
            finally:
                if connection is not None:
                    connection.close()

            extra_user_roles = ROLE_DBA
        else:
            extra_user_roles = config.get("extra-user-roles", "")
        logger.info(
            f"User is {user}, database is {database}, extra user roles are '{extra_user_roles}'"
        )
        attributes = roles_attributes(predefined_roles, extra_user_roles)
        logger.info(f"Attributes for user {user}: '{attributes}'")
        message_prefix = f"Checking that {user} user ({'with extra user roles: ' + extra_user_roles.replace(',', ', ') if extra_user_roles else 'without extra user roles'})"
        for database_to_test in databases:
            connection = None
            cursor = None
            operator_connection = None
            operator_cursor = None
            try:
                # TODO: remove conditions based on CREATEDB.
                # TODO: validate access to system databases.
                # TODO: test backup role.
                connect_permission = attributes["permissions"]["connect"]
                set_user_permission = attributes["permissions"]["set-user"]
                if (
                    (
                        connect_permission == RoleAttributeValue.ALL_DATABASES
                        and (
                            (
                                "CREATEDB" in extra_user_roles
                                and database_to_test not in ["postgres", "template0"]
                            )
                            or database_to_test not in ["postgres", "template0", "template1"]
                            or (
                                set_user_permission == RoleAttributeValue.YES
                                and database_to_test == "template1"
                            )
                        )
                    )
                    or (
                        connect_permission == RoleAttributeValue.REQUESTED_DATABASE
                        and database_to_test == database
                    )
                    or database_to_test == OTHER_DATABASE_NAME
                ):
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
                        if (
                            auto_escalate_to_database_owner == RoleAttributeValue.ALL_DATABASES
                            and database_to_test not in [OTHER_DATABASE_NAME, "template1"]
                        ) or (
                            auto_escalate_to_database_owner
                            == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        ):
                            logger.info(
                                f"{message_prefix} auto escalates to {database_owner_user}"
                            )
                            check_connected_user(cursor, user, database_owner_user)
                        else:
                            logger.info(
                                f"{message_prefix} doesn't auto escalate to {database_owner_user}"
                            )
                            check_connected_user(cursor, user, user)

                    # Test escalation to the database owner user.
                    escalate_to_database_owner_permission = attributes["permissions"][
                        "escalate-to-database-owner"
                    ]
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_user;")
                        previous_current_user = cursor.fetchone()[0]
                        cursor.execute("RESET ROLE;")
                        check_connected_user(cursor, user, user)
                    if (
                        (
                            escalate_to_database_owner_permission
                            == RoleAttributeValue.ALL_DATABASES
                            and database_to_test not in [OTHER_DATABASE_NAME, "template1"]
                        )
                        or (
                            escalate_to_database_owner_permission
                            == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                    ):
                        logger.info(
                            f"{message_prefix} can escalate to {database_owner_user}"
                        )
                        with connection.cursor() as cursor:
                            cursor.execute(SQL("SET ROLE {};").format(Identifier(database_owner_user)))
                            check_connected_user(cursor, user, database_owner_user)
                    elif database_to_test not in [OTHER_DATABASE_NAME, "template1"]:  # Because there is not charmed_database_owner role in those databases.
                        logger.info(
                            f"{message_prefix} can't escalate to {database_owner_user}"
                        )
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(SQL("SET ROLE {};").format(Identifier(database_owner_user)))
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_user;")
                        current_user = cursor.fetchone()[0]
                        if current_user != previous_current_user:
                            cursor.execute(SQL("SET ROLE {};").format(Identifier(previous_current_user)))

                    # Test objects creation.
                    # TODO: test function creation/change.
                    # TODO: test view change.
                    # TODO: test sequence creation.
                    # TODO: test function creation.
                    # TODO: test index creation.
                    create_objects_permission = attributes["permissions"]["create-objects"]
                    schema_name = f"{user}_schema"
                    create_schema_statement = SQL("CREATE SCHEMA {};").format(
                        Identifier(schema_name)
                    )
                    create_table_statement = SQL("CREATE TABLE {}.test_table(value TEXT);").format(
                        Identifier(schema_name)
                    )
                    create_table_in_public_schema_statement = SQL(
                        "CREATE TABLE public.{}(value TEXT);"
                    ).format(Identifier(f"test_table_{user}"))
                    create_view_statement = SQL("CREATE VIEW {}.test_view AS SELECT * FROM {}.test_table;").format(
                        Identifier(schema_name), Identifier(schema_name)
                    )
                    create_view_in_public_schema_statement = SQL(
                        "CREATE VIEW public.{} AS SELECT * FROM public.{};"
                    ).format(Identifier(f"test_view_{user}"), Identifier(f"test_table_{user}"))
                    if (
                        (
                            create_objects_permission == RoleAttributeValue.ALL_DATABASES
                            and database_to_test not in [OTHER_DATABASE_NAME, "template1"]
                        )
                        or (
                            create_objects_permission == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                        or (
                            escalate_to_database_owner_permission
                            == RoleAttributeValue.ALL_DATABASES
                            and database_to_test not in [OTHER_DATABASE_NAME, "template1"]
                        )
                        or (
                            escalate_to_database_owner_permission
                            == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                    ):
                        with connection.cursor() as cursor:
                            if (
                                (
                                    escalate_to_database_owner_permission
                                    == RoleAttributeValue.REQUESTED_DATABASE
                                    and database_to_test == database
                                )
                                or escalate_to_database_owner_permission
                                == RoleAttributeValue.ALL_DATABASES
                            ) and auto_escalate_to_database_owner == RoleAttributeValue.NO:
                                cursor.execute(
                                    SQL("SET ROLE {};").format(Identifier(database_owner_user))
                                )
                            logger.info(f"{message_prefix} can create schemas")
                            cursor.execute(create_schema_statement)
                            logger.info(f"{message_prefix} can create tables")
                            cursor.execute(create_table_statement)
                            logger.info(f"{message_prefix} can create tables in public schema")
                            cursor.execute(create_table_in_public_schema_statement)
                            logger.info(f"{message_prefix} can create view")
                            cursor.execute(create_view_statement)
                            logger.info(f"{message_prefix} can create views in public schema")
                            cursor.execute(create_view_in_public_schema_statement)
                    else:
                        operator_connection = db_connect(
                            host, operator_password, database=database_to_test
                        )
                        operator_connection.autocommit = True
                        operator_cursor = operator_connection.cursor()

                        logger.info(f"{message_prefix} can't create schemas")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(create_schema_statement)
                        operator_cursor.execute(create_schema_statement)

                        logger.info(f"{message_prefix} can't create tables")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(create_table_statement)
                        operator_cursor.execute(create_table_statement)

                        logger.info(f"{message_prefix} can't create tables in public schema")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(create_table_in_public_schema_statement)
                        operator_cursor.execute(create_table_in_public_schema_statement)

                        logger.info(f"{message_prefix} can't create views")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(create_view_statement)
                        operator_cursor.execute(create_view_statement)

                        logger.info(f"{message_prefix} can't create views in public schema")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(create_view_in_public_schema_statement)
                        operator_cursor.execute(create_view_in_public_schema_statement)

                        operator_cursor.close()
                        operator_cursor = None
                        operator_connection.close()
                        operator_connection = None

                    # Test write permissions.
                    write_data_permission = attributes["permissions"]["write-data"]
                    insert_statement = SQL("INSERT INTO {}.test_table VALUES ('test');").format(
                        Identifier(schema_name)
                    )
                    update_statement = SQL(
                        "UPDATE {}.test_table SET value = 'updated' WHERE value = 'test';"
                    ).format(Identifier(schema_name))
                    delete_statement = SQL(
                        "DELETE FROM {}.test_table WHERE value = 'updated';"
                    ).format(Identifier(schema_name))
                    insert_in_public_schema_statement = SQL(
                        "INSERT INTO public.{} VALUES ('test');"
                    ).format(Identifier(f"test_table_{user}"))
                    if (
                        write_data_permission == RoleAttributeValue.ALL_DATABASES
                        or (
                            write_data_permission == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                        or escalate_to_database_owner_permission
                        == RoleAttributeValue.ALL_DATABASES
                        or (
                            escalate_to_database_owner_permission
                            == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                    ):
                        with connection.cursor() as cursor:
                            logger.info(
                                f"{message_prefix} can write to tables in {schema_name} schema"
                            )
                            if database_to_test not in [OTHER_DATABASE_NAME, "template1"] and (
                                (
                                    (
                                        escalate_to_database_owner_permission
                                        == RoleAttributeValue.REQUESTED_DATABASE
                                        and database_to_test == database
                                    )
                                    or escalate_to_database_owner_permission
                                    == RoleAttributeValue.ALL_DATABASES
                                )
                                and auto_escalate_to_database_owner == RoleAttributeValue.NO
                            ):
                                cursor.execute(
                                    SQL("SET ROLE {};").format(Identifier(database_owner_user))
                                )
                            cursor.execute(insert_statement)
                            cursor.execute(update_statement)
                            cursor.execute(delete_statement)
                            logger.info(f"{message_prefix} can write to tables in public schema")
                            cursor.execute(insert_in_public_schema_statement)
                    else:
                        logger.info(
                            f"{message_prefix} can't write to tables in {schema_name} schema"
                        )
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(insert_statement)
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(update_statement)
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(delete_statement)
                        logger.info(f"{message_prefix} can't write to tables in public schema")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(insert_in_public_schema_statement)

                    # Test read permissions.
                    # TODO: read views.
                    # TODO: read sequences.
                    read_data_permission = attributes["permissions"]["read-data"]
                    select_statement = SQL("SELECT * FROM {}.test_table;").format(
                        Identifier(schema_name)
                    )
                    select_in_public_schema_statement = SQL("SELECT * FROM public.{};").format(
                        Identifier(f"test_table_{user}")
                    )
                    if (
                        read_data_permission == RoleAttributeValue.ALL_DATABASES
                        or (
                            read_data_permission == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                        or escalate_to_database_owner_permission
                        == RoleAttributeValue.ALL_DATABASES
                        or (
                            escalate_to_database_owner_permission
                            == RoleAttributeValue.REQUESTED_DATABASE
                            and database_to_test == database
                        )
                    ):
                        with connection.cursor() as cursor:
                            if database_to_test not in [OTHER_DATABASE_NAME, "template1"] and (
                                (
                                    (
                                        escalate_to_database_owner_permission
                                        == RoleAttributeValue.REQUESTED_DATABASE
                                        and database_to_test == database
                                    )
                                    or escalate_to_database_owner_permission
                                    == RoleAttributeValue.ALL_DATABASES
                                )
                                and auto_escalate_to_database_owner == RoleAttributeValue.NO
                            ):
                                cursor.execute(
                                    SQL("SET ROLE {};").format(Identifier(database_owner_user))
                                )
                            logger.info(
                                f"{message_prefix} can read from tables in {schema_name} schema"
                            )
                            cursor.execute(select_statement)
                            logger.info(f"{message_prefix} can read from tables in public schema")
                            cursor.execute(select_in_public_schema_statement)
                    else:
                        logger.info(
                            f"{message_prefix} can't read from tables in {schema_name} schema"
                        )
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(select_statement)
                        logger.info(f"{message_prefix} can't read from tables in public schema")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute(select_in_public_schema_statement)

                    if attributes["permissions"]["read-stats"] == RoleAttributeValue.ALL_DATABASES:
                        logger.info(f"{message_prefix} can read stats in all databases")
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT * FROM pg_stat_activity;")
                    else:
                        logger.info(f"{message_prefix} can't read stats in all databases")
                        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT * FROM pg_stat_activity;")

                    # TODO: execute functions.

                    if set_user_permission == RoleAttributeValue.YES and database_to_test not in [
                        OTHER_DATABASE_NAME,
                        "template1",
                    ]:
                        logger.info(f"{message_prefix} can call the set_user function")
                        with connection.cursor() as cursor:
                            cursor.execute("RESET ROLE;")
                            cursor.execute("SELECT set_user('rewind'::TEXT);")
                            check_connected_user(cursor, user, "rewind")
                            cursor.execute("SELECT reset_user();")
                            check_connected_user(cursor, user, user)
                            cursor.execute("SELECT set_user_u('operator'::TEXT);")
                            check_connected_user(cursor, user, "operator")
                            cursor.execute("SELECT reset_user();")
                            check_connected_user(cursor, user, user)
                    else:
                        logger.info(f"{message_prefix} can't call the set_user function")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute("RESET ROLE;")
                            cursor.execute("SELECT set_user('rewind'::TEXT);")
                        with (
                            pytest.raises(psycopg2.errors.InsufficientPrivilege),
                            connection.cursor() as cursor,
                        ):
                            cursor.execute("RESET ROLE;")
                            cursor.execute("SELECT set_user_u('operator'::TEXT);")

                    # Do the following operations only once.
                    if database_to_test == database:
                        # Test permission to call the set_up_predefined_catalog_roles function.
                        statement = "SELECT set_up_predefined_catalog_roles();"
                        if (
                            attributes["permissions"]["set-up-predefined-catalog-roles"]
                            == RoleAttributeValue.YES
                        ):
                            logger.info(
                                f"{message_prefix} can call the set-up-predefined-catalog-roles function"
                            )
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    SQL("SET ROLE {};").format(Identifier(ROLE_DATABASES_OWNER))
                                )
                                cursor.execute(statement)
                        else:
                            logger.info(
                                f"{message_prefix} can't call the set-up-predefined-catalog-roles function"
                            )
                            with (
                                pytest.raises(psycopg2.errors.InsufficientPrivilege),
                                connection.cursor() as cursor,
                            ):
                                cursor.execute(statement)

                        # Test database creation, change and removal.
                        cursor = connection.cursor()
                        new_database_name = f"{OTHER_DATABASE_NAME}-{user}"
                        create_database_statement = SQL("CREATE DATABASE {};").format(
                            Identifier(new_database_name)
                        )
                        first_alter_database_statement = SQL(
                            "ALTER DATABASE {} RENAME TO {};"
                        ).format(
                            Identifier(new_database_name), Identifier(f"{new_database_name}-1")
                        )
                        second_alter_database_statement = SQL(
                            "ALTER DATABASE {} RENAME TO {};"
                        ).format(
                            Identifier(f"{new_database_name}-1"), Identifier(new_database_name)
                        )
                        first_drop_database_statement = SQL("DROP DATABASE {};").format(
                            Identifier(new_database_name)
                        )
                        second_drop_database_statement = SQL("DROP DATABASE {};").format(
                            Identifier(OTHER_DATABASE_NAME)
                        )
                        if attributes["permissions"]["create-databases"] == RoleAttributeValue.YES:
                            logger.info(f"{message_prefix} can create databases")
                            cursor.execute(create_database_statement)
                            logger.info(f"{message_prefix} can alter databases")
                            cursor.execute(first_alter_database_statement)
                            cursor.execute(second_alter_database_statement)
                            logger.info(f"{message_prefix} can drop databases owned by the user")
                            cursor.execute(first_drop_database_statement)
                            logger.info(
                                f"{message_prefix} can't drop databases not owned by the user"
                            )
                            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                                cursor.execute(second_drop_database_statement)
                        else:
                            logger.info(f"{message_prefix} can't create databases")
                            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                                cursor.execute(create_database_statement)

                            operator_connection = db_connect(
                                host, operator_password, database=database_to_test
                            )
                            operator_connection.autocommit = True
                            operator_cursor = operator_connection.cursor()
                            operator_cursor.execute(create_database_statement)
                            operator_cursor.close()
                            operator_cursor = None
                            operator_connection.close()
                            operator_connection = None

                            logger.info(f"{message_prefix} can't alter databases")
                            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                                cursor.execute(first_alter_database_statement)
                            logger.info(f"{message_prefix} can't drop databases")
                            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                                cursor.execute(first_drop_database_statement)
            finally:
                if cursor is not None:
                    cursor.close()
                if connection is not None:
                    connection.close()
                if operator_cursor is not None:
                    operator_cursor.close()
                if operator_connection is not None:
                    operator_connection.close()
