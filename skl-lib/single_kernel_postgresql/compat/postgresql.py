# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PostgreSQL helper class.

The `postgresql` module provides methods for interacting with the PostgreSQL instance.

Any charm using this library should import the `psycopg2` or `psycopg2-binary` dependency.
"""

import logging
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Tuple

import psycopg2
import psycopg2.errors
import psycopg2.extensions
from psycopg2.sql import SQL, Identifier, Literal

from ..config.literals import (
    BACKUP_USER,
    SYSTEM_USERS,
)

ACCESS_GROUP_RELATION = "relation_access"

ROLE_STATS = "charmed_stats"
ROLE_READ = "charmed_read"
ROLE_DML = "charmed_dml"
ROLE_BACKUP = "charmed_backup"
ROLE_DBA = "charmed_dba"
ROLE_ADMIN = "charmed_admin"
ROLE_DATABASES_OWNER = "charmed_databases_owner"
ALLOWED_ROLES = {
    ROLE_STATS,
    ROLE_READ,
    ROLE_DML,
    ROLE_ADMIN,
}

INVALID_DATABASE_NAME_BLOCKING_MESSAGE = "invalid database name"
INVALID_DATABASE_NAMES = ["databases", "postgres", "template0", "template1"]
INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE = "invalid role(s) for extra user roles"

REQUIRED_PLUGINS = {
    "address_standardizer": ["postgis"],
    "address_standardizer_data_us": ["postgis"],
    "jsonb_plperl": ["plperl"],
    "postgis_raster": ["postgis"],
    "postgis_tiger_geocoder": ["postgis", "fuzzystrmatch"],
    "postgis_topology": ["postgis"],
}
DEPENDENCY_PLUGINS = set()
for dependencies in REQUIRED_PLUGINS.values():
    DEPENDENCY_PLUGINS |= set(dependencies)

logger = logging.getLogger(__name__)


class PostgreSQLBaseError(Exception):
    """Base lib exception."""

    message = None


class PostgreSQLCreateDatabaseError(PostgreSQLBaseError):
    """Exception raised when creating a database fails."""

    def __init__(self, message: Optional[str] = None):
        super().__init__(message)
        self.message = message


class PostgreSQLCreateUserError(PostgreSQLBaseError):
    """Exception raised when creating a user fails."""

    def __init__(self, message: Optional[str] = None):
        super().__init__(message)
        self.message = message


class PostgreSQLUndefinedHostError(PostgreSQLBaseError):
    """Exception when host is not set."""


class PostgreSQLUndefinedPasswordError(PostgreSQLBaseError):
    """Exception when password is not set."""


class PostgreSQLDeleteUserError(PostgreSQLBaseError):
    """Exception raised when deleting a user fails."""


class PostgreSQLListUsersError(PostgreSQLBaseError):
    """Exception raised when retrieving PostgreSQL users list fails."""


class PostgreSQLGetPostgreSQLVersionError(PostgreSQLBaseError):
    """Exception raised when retrieving PostgreSQL version fails."""


class PostgreSQLEnableDisableExtensionError(PostgreSQLBaseError):
    """Exception raised when enabling/disabling an extension fails."""


class PostgreSQLBase:
    """Class to encapsulate all operations related to interacting with PostgreSQL instance."""

    def __init__(
        self,
        primary_host: Optional[str],
        current_host: Optional[str],
        user: str,
        password: Optional[str],
        database: str,
    ):
        """Create a PostgreSQL helper.

        Args:
            primary_host: hostname or address for primary database host.
            current_host: hostname or address for the current database host.
            user: username to connect as.
            password: password for the user.
            database: default database name.
        """
        self.primary_host = primary_host
        self.current_host = current_host
        self.user = user
        self.password = password
        self.database = database

    def _configure_pgaudit(self, enable: bool) -> None:
        connection = None
        try:
            connection = self._connect_to_database()
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE;")
                if enable:
                    cursor.execute("ALTER SYSTEM SET pgaudit.log = 'ROLE,DDL,MISC,MISC_SET';")
                    cursor.execute("ALTER SYSTEM SET pgaudit.log_client TO off;")
                    cursor.execute("ALTER SYSTEM SET pgaudit.log_parameter TO off;")
                else:
                    cursor.execute("ALTER SYSTEM RESET pgaudit.log;")
                    cursor.execute("ALTER SYSTEM RESET pgaudit.log_client;")
                    cursor.execute("ALTER SYSTEM RESET pgaudit.log_parameter;")
                cursor.execute("SELECT pg_reload_conf();")
        finally:
            if connection is not None:
                connection.close()

    def _connect_to_database(
        self, database: Optional[str] = None, database_host: Optional[str] = None
    ) -> psycopg2.extensions.connection:
        """Creates a connection to the database.

        Args:
            database: database to connect to (defaults to the database
                provided when the object for this class was created).
            database_host: host to connect to instead of the primary host.

        Returns:
             psycopg2 connection object.
        """
        host = database_host if database_host is not None else self.primary_host
        if not host:
            raise PostgreSQLUndefinedHostError("Host not set")
        if not self.password:
            raise PostgreSQLUndefinedPasswordError("Password not set")

        dbname = database if database else self.database
        logger.debug(
            f"New DB connection: dbname='{dbname}' user='{self.user}' host='{host}' connect_timeout=1"
        )
        connection = psycopg2.connect(
            f"dbname='{dbname}' user='{self.user}' host='{host}'"
            f"password='{self.password}' connect_timeout=1"
        )
        connection.autocommit = True
        return connection

    def create_database(
        self,
        database: str,
        plugins: Optional[List[str]] = None,
    ) -> None:
        """Creates a new database and grant privileges to a user on it.

        Args:
            database: database to be created.
            plugins: extensions to enable in the new database.
        """
        # The limit of 49 characters for the database name is due to the usernames that
        # are created for each database, which have the prefix `charmed_` and a suffix
        # like `_owner`, which summed to the database name must not exceed PostgreSQL
        # maximum identifier length (63 characters, which is, the prefix, 8 characters,
        # + database name, 49 characters maximum, + suffix, 6 characters).
        if len(database) > 49:
            logger.error(f"Invalid database name (it must not exceed 49 characters): {database}.")
            raise PostgreSQLCreateDatabaseError(INVALID_DATABASE_NAME_BLOCKING_MESSAGE)
        if database in INVALID_DATABASE_NAMES:
            logger.error(f"Invalid database name: {database}.")
            raise PostgreSQLCreateDatabaseError(INVALID_DATABASE_NAME_BLOCKING_MESSAGE)
        plugins = plugins if plugins else []
        try:
            connection = self._connect_to_database()
            cursor = connection.cursor()
            cursor.execute(
                SQL("SELECT datname FROM pg_database WHERE datname={};").format(Literal(database))
            )
            if cursor.fetchone() is None:
                cursor.execute(SQL("SET ROLE {};").format(Identifier(ROLE_DATABASES_OWNER)))
                cursor.execute(SQL("CREATE DATABASE {};").format(Identifier(database)))
                cursor.execute(
                    SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC;").format(
                        Identifier(database)
                    )
                )
            with self._connect_to_database(database=database) as conn, conn.cursor() as curs:
                curs.execute(SQL("SET ROLE {};").format(Identifier(ROLE_DATABASES_OWNER)))
                curs.execute(SQL("SELECT set_up_predefined_catalog_roles();"))
        except psycopg2.Error as e:
            logger.error(f"Failed to create database: {e}")
            raise PostgreSQLCreateDatabaseError() from e

        # Enable preset extensions
        if plugins:
            self.enable_disable_extensions(dict.fromkeys(plugins, True), database)

    def create_user(
        self,
        user: str,
        password: Optional[str] = None,
        admin: bool = False,
        replication: bool = False,
        extra_user_roles: Optional[List[str]] = None,
        database: Optional[str] = None,
        can_create_database: bool = False,
    ) -> None:
        """Creates a database user.

        Args:
            user: user to be created.
            password: password to be assigned to the user.
            admin: whether the user should have additional admin privileges.
            replication: whether the user should have replication privileges.
            extra_user_roles: additional privileges and/or roles to be assigned to the user.
            database: optional database to allow the user to connect to.
            can_create_database: whether the user should be able to create databases.
        """
        try:
            roles, privileges = self._process_extra_user_roles(user, extra_user_roles)

            with self._connect_to_database() as connection, connection.cursor() as cursor:
                # Create or update the user.
                cursor.execute(
                    SQL("SELECT TRUE FROM pg_roles WHERE rolname={};").format(Literal(user))
                )
                if cursor.fetchone() is not None:
                    user_definition = "ALTER ROLE {} "
                    altering = True
                else:
                    user_definition = "CREATE ROLE {} "
                    altering = False
                user_definition += f"WITH LOGIN{' SUPERUSER' if admin else ''}{' REPLICATION' if replication else ''} ENCRYPTED PASSWORD '{password}'"
                if not altering:
                    user_definition, connect_statements = self._adjust_user_definition(
                        user, roles, database, user_definition
                    )
                    if can_create_database:
                        user_definition += " CREATEDB"
                    if privileges:
                        user_definition += f" {' '.join(privileges)}"
                else:
                    db_roles, connect_statements = self._adjust_user_roles(user, roles, database)
                    roles = [*db_roles, *roles]
                cursor.execute(SQL("RESET ROLE;"))
                cursor.execute(SQL("BEGIN;"))
                cursor.execute(SQL("SET LOCAL log_statement = 'none';"))
                cursor.execute(SQL(f"{user_definition};").format(Identifier(user)))
                cursor.execute(SQL("COMMIT;"))
                if len(connect_statements) > 0:
                    for connect_statement in connect_statements:
                        cursor.execute(connect_statement)

                # Add extra user roles to the new user.
                for role in roles:
                    cursor.execute(
                        SQL("GRANT {} TO {};").format(Identifier(role), Identifier(user))
                    )
        except psycopg2.Error as e:
            logger.error(f"Failed to create user: {e}")
            raise PostgreSQLCreateUserError() from e

    def _adjust_user_definition(
        self, user: str, roles: Optional[List[str]], database: Optional[str], user_definition: str
    ) -> Tuple[str, List[str]]:
        """Adjusts the user definition to include additional statements.

        Returns:
            A tuple containing the adjusted user definition and a list of additional statements.
        """
        db_roles, connect_statements = self._adjust_user_roles(user, roles, database)
        if db_roles:
            str_roles = [f'"{role}"' for role in db_roles]
            user_definition += f" IN ROLE {', '.join(str_roles)}"
        return user_definition, connect_statements

    def _adjust_user_roles(
        self, user: str, roles: Optional[List[str]], database: Optional[str]
    ) -> Tuple[List[str], List[str]]:
        """Adjusts the user definition to include additional statements.

        Returns:
            A tuple containing the adjusted user definition and a list of additional statements.
        """
        db_roles = []
        connect_statements = []
        if database:
            if roles is not None and not any(
                role in [ROLE_STATS, ROLE_READ, ROLE_DML, ROLE_BACKUP, ROLE_DBA] for role in roles
            ):
                db_roles.append(f"charmed_{database}_admin")
                db_roles.append(f"charmed_{database}_dml")
            else:
                connect_statements.append(
                    SQL("GRANT CONNECT ON DATABASE {} TO {};").format(
                        Identifier(database), Identifier(user)
                    )
                )
        if roles is not None and any(
            role
            in [
                ROLE_STATS,
                ROLE_READ,
                ROLE_DML,
                ROLE_BACKUP,
                ROLE_DBA,
                ROLE_ADMIN,
                ROLE_DATABASES_OWNER,
            ]
            for role in roles
        ):
            for system_database in ["postgres", "template1"]:
                connect_statements.append(
                    SQL("GRANT CONNECT ON DATABASE {} TO {};").format(
                        Identifier(system_database), Identifier(user)
                    )
                )
        return db_roles, connect_statements

    def _process_extra_user_roles(
        self, user: str, extra_user_roles: Optional[List[str]] = None
    ) -> Tuple[List[str], Set[str]]:
        # Separate roles and privileges from the provided extra user roles.
        roles = []
        privileges = set()
        if extra_user_roles:
            if len(extra_user_roles) > 2 and sorted(extra_user_roles) != [
                ROLE_ADMIN,
                "createdb",
                ACCESS_GROUP_RELATION,
            ]:
                extra_user_roles.remove(ACCESS_GROUP_RELATION)
                logger.error(
                    "Invalid extra user roles: "
                    f"{', '.join(extra_user_roles)}. "
                    f"Only 'createdb' and '{ROLE_ADMIN}' are allowed together."
                )
                raise PostgreSQLCreateUserError(INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE)
            valid_privileges, valid_roles = self.list_valid_privileges_and_roles()
            roles = [
                role
                for role in extra_user_roles
                if (
                    user == BACKUP_USER
                    or user in SYSTEM_USERS
                    or role in valid_roles
                    or role == ACCESS_GROUP_RELATION
                    or role == "createdb"
                )
            ]
            if "createdb" in extra_user_roles:
                extra_user_roles.remove("createdb")
                roles.remove("createdb")
                extra_user_roles.append(ROLE_DATABASES_OWNER)
                roles.append(ROLE_DATABASES_OWNER)
            privileges = {
                extra_user_role
                for extra_user_role in extra_user_roles
                if extra_user_role and extra_user_role not in roles
            }
            invalid_privileges = [
                privilege for privilege in privileges if privilege not in valid_privileges
            ]
            if len(invalid_privileges) > 0:
                logger.error(f"Invalid extra user roles: {', '.join(privileges)}")
                raise PostgreSQLCreateUserError(INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE)
        return roles, privileges

    def delete_user(self, user: str) -> None:
        """Deletes a database user.

        Args:
            user: user to be deleted.
        """
        # First of all, check whether the user exists. Otherwise, do nothing.
        users = self.list_users()
        if user not in users:
            return

        # List all databases.
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT datname FROM pg_database WHERE datistemplate = false;")
                databases = [row[0] for row in cursor.fetchall()]

            # Existing objects need to be reassigned in each database
            # before the user can be deleted.
            for database in databases:
                with self._connect_to_database(
                    database
                ) as connection, connection.cursor() as cursor:
                    cursor.execute(SQL("RESET ROLE;"))
                    cursor.execute(
                        SQL("REASSIGN OWNED BY {} TO {};").format(
                            Identifier(user), Identifier(self.user)
                        )
                    )
                    cursor.execute(SQL("DROP OWNED BY {};").format(Identifier(user)))

            # Delete the user.
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(SQL("RESET ROLE;"))
                cursor.execute(SQL("DROP ROLE {};").format(Identifier(user)))
        except psycopg2.Error as e:
            logger.error(f"Failed to delete user: {e}")
            raise PostgreSQLDeleteUserError() from e

    def enable_disable_extensions(
        self, extensions: Dict[str, bool], database: Optional[str] = None
    ) -> None:
        """Enables or disables a PostgreSQL extension.

        Args:
            extensions: the name of the extensions.
            database: optional database where to enable/disable the extension.

        Raises:
            PostgreSQLEnableDisableExtensionError if the operation fails.
        """
        connection = None
        try:
            if database is not None:
                databases = [database]
            else:
                # Retrieve all the databases.
                with self._connect_to_database() as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT datname FROM pg_database WHERE NOT datistemplate;")
                    databases = {database[0] for database in cursor.fetchall()}

            ordered_extensions = OrderedDict()
            for plugin in DEPENDENCY_PLUGINS:
                ordered_extensions[plugin] = extensions.get(plugin, False)
            for extension, enable in extensions.items():
                ordered_extensions[extension] = enable

            self._configure_pgaudit(False)

            # Enable/disabled the extension in each database.
            for database in databases:
                with self._connect_to_database(
                    database=database
                ) as connection, connection.cursor() as cursor:
                    for extension, enable in ordered_extensions.items():
                        cursor.execute(
                            f"CREATE EXTENSION IF NOT EXISTS {extension};"
                            if enable
                            else f"DROP EXTENSION IF EXISTS {extension};"
                        )
            self._configure_pgaudit(ordered_extensions.get("pgaudit", False))
        except psycopg2.errors.UniqueViolation:  # type: ignore
            pass
        except psycopg2.errors.DependentObjectsStillExist:  # type: ignore
            raise
        except psycopg2.Error as e:
            raise PostgreSQLEnableDisableExtensionError() from e
        finally:
            if connection is not None:
                connection.close()

    def get_postgresql_version(self, current_host=True) -> str:
        """Returns the PostgreSQL version.

        Returns:
            PostgreSQL version number.
        """
        host = self.current_host if current_host else None
        try:
            with self._connect_to_database(
                database_host=host
            ) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT version();")
                # Split to get only the version number. There should always be a version.
                return cursor.fetchone()[0].split(" ")[1]
        except psycopg2.Error as e:
            logger.error(f"Failed to get PostgreSQL version: {e}")
            raise PostgreSQLGetPostgreSQLVersionError() from e

    def list_users(self, group: Optional[str] = None, current_host=False) -> Set[str]:
        """Returns the list of PostgreSQL database users.

        Args:
            group: optional group to filter the users.
            current_host: whether to check the current host
                instead of the primary host.

        Returns:
            List of PostgreSQL database users.
        """
        connection = None
        host = self.current_host if current_host else None
        try:
            with self._connect_to_database(
                database_host=host
            ) as connection, connection.cursor() as cursor:
                if group:
                    query = SQL(
                        "SELECT usename FROM (SELECT UNNEST(grolist) AS user_id FROM pg_catalog.pg_group WHERE groname = {}) AS g JOIN pg_catalog.pg_user AS u ON g.user_id = u.usesysid;"
                    ).format(Literal(group))
                else:
                    query = "SELECT usename FROM pg_catalog.pg_user;"
                cursor.execute(query)
                usernames = cursor.fetchall()
                return {username[0] for username in usernames}
        except psycopg2.Error as e:
            logger.error(f"Failed to list PostgreSQL database users: {e}")
            raise PostgreSQLListUsersError() from e
        finally:
            if connection is not None:
                connection.close()

    def list_valid_privileges_and_roles(self) -> Tuple[Set[str], Set[str]]:
        """Returns two sets with valid privileges and roles.

        Returns:
            Tuple containing two sets: the first with valid privileges
                and the second with valid roles.
        """
        return {
            "superuser",
        }, ALLOWED_ROLES

    def is_user_in_hba(self, username: str) -> bool:
        """Check if user was added in pg_hba."""
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "SELECT COUNT(*) FROM pg_hba_file_rules WHERE {} = ANY(user_name);"
                    ).format(Literal(username))
                )
                if result := cursor.fetchone():
                    return result[0] > 0
                return False
        except psycopg2.Error as e:
            logger.debug(f"Failed to check pg_hba: {e}")
            return False
        finally:
            if connection:
                connection.close()
