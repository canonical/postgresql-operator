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

import psycopg2
from psycopg2 import sql

# The unique Charmhub library identifier, never change it
LIBID = "24ee217a54e840a598ff21a079c3e678"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


logger = logging.getLogger(__name__)


class PostgreSQLCreateDatabaseError(Exception):
    """Exception raised when creating a database fails."""


class PostgreSQLCreateUserError(Exception):
    """Exception raised when creating a user fails."""


class PostgreSQLDeleteUserError(Exception):
    """Exception raised when deleting a user fails."""


class PostgreSQLGetPostgreSQLVersionError(Exception):
    """Exception raised when retrieving PostgreSQL version fails."""


class PostgreSQL:
    """Class to encapsulate all operations related to interacting with PostgreSQL instance."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: str,
    ):
        self.host = host
        self.user = user
        self.password = password
        self.database = database

    def _connect_to_database(self, database: str = None) -> psycopg2.extensions.connection:
        """Creates a connection to the database.

        Args:
            database: database to connect to (defaults to the database
                provided when the object for this class was created).

        Returns:
             psycopg2 connection object.
        """
        connection = psycopg2.connect(
            f"dbname='{database if database else self.database}' user='{self.user}' host='{self.host}' password='{self.password}' connect_timeout=1"
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
            extra_user_roles: additional roles to be assigned to the user.
        """
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT TRUE FROM pg_roles WHERE rolname='{user}';")
                user_definition = f"{user} WITH LOGIN{' SUPERUSER' if admin else ''} ENCRYPTED PASSWORD '{password}'"
                if extra_user_roles:
                    user_definition += f' {extra_user_roles.replace(",", " ")}'
                if cursor.fetchone() is not None:
                    cursor.execute(f"ALTER ROLE {user_definition};")
                else:
                    cursor.execute(f"CREATE ROLE {user_definition};")
        except psycopg2.Error as e:
            logger.error(f"Failed to create user: {e}")
            raise PostgreSQLCreateUserError()

    def delete_user(self, user: str) -> None:
        """Deletes a database user.

        Args:
            user: user to be deleted.
        """
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
                    cursor.execute(f"REASSIGN OWNED BY {user} TO postgres;")
                    cursor.execute(f"DROP OWNED BY {user};")

            # Delete the user.
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(f"DROP ROLE {user};")
        except psycopg2.Error as e:
            logger.error(f"Failed to delete user: {e}")
            raise PostgreSQLDeleteUserError()

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
