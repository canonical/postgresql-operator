# Copyright 2022 Canonical Ltd.
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
from typing import Set

import psycopg2
from psycopg2 import sql

# The unique Charmhub library identifier, never change it
LIBID = "24ee217a54e840a598ff21a079c3e678"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 9


logger = logging.getLogger(__name__)


class PostgreSQLCreateDatabaseError(Exception):
    """Exception raised when creating a database fails."""


class PostgreSQLCreateUserError(Exception):
    """Exception raised when creating a user fails."""


class PostgreSQLDeleteUserError(Exception):
    """Exception raised when deleting a user fails."""


class PostgreSQLEnableDisableExtensionError(Exception):
    """Exception raised when enabling/disabling an extension fails."""


class PostgreSQLGetPostgreSQLVersionError(Exception):
    """Exception raised when retrieving PostgreSQL version fails."""


class PostgreSQLListUsersError(Exception):
    """Exception raised when retrieving PostgreSQL users list fails."""


class PostgreSQLUpdateUserPasswordError(Exception):
    """Exception raised when updating a user password fails."""


class PostgreSQL:
    """Class to encapsulate all operations related to interacting with PostgreSQL instance."""

    def __init__(
        self,
        primary_host: str,
        current_host: str,
        user: str,
        password: str,
        database: str,
    ):
        self.primary_host = primary_host
        self.current_host = current_host
        self.user = user
        self.password = password
        self.database = database

    def _connect_to_database(
        self, database: str = None, connect_to_current_host: bool = False
    ) -> psycopg2.extensions.connection:
        """Creates a connection to the database.

        Args:
            database: database to connect to (defaults to the database
                provided when the object for this class was created).
            connect_to_current_host: whether to connect to the current host
                instead of the primary host.

        Returns:
             psycopg2 connection object.
        """
        host = self.current_host if connect_to_current_host else self.primary_host
        connection = psycopg2.connect(
            f"dbname='{database if database else self.database}' user='{self.user}' host='{host}'"
            f"password='{self.password}' connect_timeout=1"
        )
        connection.autocommit = True
        return connection

    def create_database(self, database: str, user: str) -> None:
        """Creates a new database and grant privileges to a user on it.

        Args:
            database: database to be created.
            user: user that will have access to the database.
        """
        try:
            connection = self._connect_to_database()
            cursor = connection.cursor()
            cursor.execute(f"SELECT datname FROM pg_database WHERE datname='{database}';")
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(database)))
            cursor.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {};").format(
                    sql.Identifier(database), sql.Identifier(user)
                )
            )
            with self._connect_to_database(database=database) as conn:
                with conn.cursor() as curs:
                    statements = []
                    curs.execute(
                        "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' and schema_name <> 'information_schema';"
                    )
                    for row in curs:
                        schema = sql.Identifier(row[0])
                        statements.append(
                            sql.SQL(
                                "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} TO {};"
                            ).format(schema, sql.Identifier(user))
                        )
                        statements.append(
                            sql.SQL(
                                "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} TO {};"
                            ).format(schema, sql.Identifier(user))
                        )
                        statements.append(
                            sql.SQL(
                                "GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA {} TO {};"
                            ).format(schema, sql.Identifier(user))
                        )
                    for statement in statements:
                        curs.execute(statement)
        except psycopg2.Error as e:
            logger.error(f"Failed to create database: {e}")
            raise PostgreSQLCreateDatabaseError()

    def create_user(
        self, user: str, password: str, admin: bool = False, extra_user_roles: str = None
    ) -> None:
        """Creates a database user.

        Args:
            user: user to be created.
            password: password to be assigned to the user.
            admin: whether the user should have additional admin privileges.
            extra_user_roles: additional privileges and/or roles to be assigned to the user.
        """
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                # Separate roles and privileges from the provided extra user roles.
                roles = privileges = None
                if extra_user_roles:
                    extra_user_roles = tuple(extra_user_roles.lower().split(","))
                    cursor.execute(
                        "SELECT rolname FROM pg_roles WHERE rolname IN %s;", (extra_user_roles,)
                    )
                    roles = [role[0] for role in cursor.fetchall()]
                    privileges = [
                        extra_user_role
                        for extra_user_role in extra_user_roles
                        if extra_user_role not in roles
                    ]

                # Create or update the user.
                cursor.execute(f"SELECT TRUE FROM pg_roles WHERE rolname='{user}';")
                if cursor.fetchone() is not None:
                    user_definition = "ALTER ROLE {}"
                else:
                    user_definition = "CREATE ROLE {}"
                user_definition += (
                    f"WITH LOGIN{' SUPERUSER' if admin else ''} ENCRYPTED PASSWORD '{password}'"
                )
                if privileges:
                    user_definition += f' {" ".join(privileges)}'
                cursor.execute(sql.SQL(f"{user_definition};").format(sql.Identifier(user)))

                # Add extra user roles to the new user.
                if roles:
                    for role in roles:
                        cursor.execute(
                            sql.SQL("GRANT {} TO {};").format(
                                sql.Identifier(role), sql.Identifier(user)
                            )
                        )
        except psycopg2.Error as e:
            logger.error(f"Failed to create user: {e}")
            raise PostgreSQLCreateUserError()

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
                    cursor.execute(
                        sql.SQL("REASSIGN OWNED BY {} TO {};").format(
                            sql.Identifier(user), sql.Identifier(self.user)
                        )
                    )
                    cursor.execute(sql.SQL("DROP OWNED BY {};").format(sql.Identifier(user)))

            # Delete the user.
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP ROLE {};").format(sql.Identifier(user)))
        except psycopg2.Error as e:
            logger.error(f"Failed to delete user: {e}")
            raise PostgreSQLDeleteUserError()

    def enable_disable_extension(self, extension: str, enable: bool, database: str = None) -> None:
        """Enables or disables a PostgreSQL extension.

        Args:
            extension: the name of the extensions.
            enable: whether the extension should be enabled or disabled.
            database: optional database where to enable/disable the extension.

        Raises:
            PostgreSQLEnableDisableExtensionError if the operation fails.
        """
        statement = (
            f"CREATE EXTENSION IF NOT EXISTS {extension};"
            if enable
            else f"DROP EXTENSION IF EXISTS {extension};"
        )
        connection = None
        try:
            if database is not None:
                databases = [database]
            else:
                # Retrieve all the databases.
                with self._connect_to_database() as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT datname FROM pg_database WHERE NOT datistemplate;")
                    databases = {database[0] for database in cursor.fetchall()}

            # Enable/disabled the extension in each database.
            for database in databases:
                with self._connect_to_database(
                    database=database
                ) as connection, connection.cursor() as cursor:
                    cursor.execute(statement)
        except psycopg2.errors.UniqueViolation:
            pass
        except psycopg2.Error:
            raise PostgreSQLEnableDisableExtensionError()
        finally:
            if connection is not None:
                connection.close()

    def get_postgresql_version(self) -> str:
        """Returns the PostgreSQL version.

        Returns:
            PostgreSQL version number.
        """
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT version();")
                # Split to get only the version number.
                return cursor.fetchone()[0].split(" ")[1]
        except psycopg2.Error as e:
            logger.error(f"Failed to get PostgreSQL version: {e}")
            raise PostgreSQLGetPostgreSQLVersionError()

    def is_tls_enabled(self, check_current_host: bool = False) -> bool:
        """Returns whether TLS is enabled.

        Args:
            check_current_host: whether to check the current host
                instead of the primary host.

        Returns:
            whether TLS is enabled.
        """
        try:
            with self._connect_to_database(
                connect_to_current_host=check_current_host
            ) as connection, connection.cursor() as cursor:
                cursor.execute("SHOW ssl;")
                return "on" in cursor.fetchone()[0]
        except psycopg2.Error:
            # Connection errors happen when PostgreSQL has not started yet.
            return False

    def list_users(self) -> Set[str]:
        """Returns the list of PostgreSQL database users.

        Returns:
            List of PostgreSQL database users.
        """
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT usename FROM pg_catalog.pg_user;")
                usernames = cursor.fetchall()
                return {username[0] for username in usernames}
        except psycopg2.Error as e:
            logger.error(f"Failed to list PostgreSQL database users: {e}")
            raise PostgreSQLListUsersError()

    def update_user_password(self, username: str, password: str) -> None:
        """Update a user password.

        Args:
            username: the user to update the password.
            password: the new password for the user.

        Raises:
            PostgreSQLUpdateUserPasswordError if the password couldn't be changed.
        """
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("ALTER USER {} WITH ENCRYPTED PASSWORD '" + password + "';").format(
                        sql.Identifier(username)
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to update user password: {e}")
            raise PostgreSQLUpdateUserPasswordError()
        finally:
            if connection is not None:
                connection.close()
