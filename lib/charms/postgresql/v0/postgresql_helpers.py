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

"""PostgreSQL's helpers Library.

This library provides common PostgreSQL-specific features for the
PostgreSQL machine and Kubernetes charms.
"""
import logging

import psycopg2
from psycopg2 import sql

logger = logging.getLogger(__name__)


def connect_to_database(
    database: str, user: str, host: str, password: str
) -> psycopg2.extensions.connection:
    """Creates an auth_query function for proxy applications.

    Args:
        database: name of the database to connect to.
        user: user used to connect to the database.
        host: address of the database server.
        password: password used to connect to the database.

    Returns:
         psycopg2 connection object.
    """
    connection = psycopg2.connect(
        f"dbname='{database}' user='{user}' host='{host}' password='{password}' connect_timeout=1"
    )
    connection.autocommit = True
    return connection


def create_database(connection: psycopg2.extensions.connection, database: str, user: str) -> None:
    """Creates a new database.

    Args:
        connection: psycopg2 connection object.
        database: database to be created.
        user: user that will have access to the database.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT datname FROM pg_database WHERE datname='{database}';")
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(database)))
        cursor.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {};").format(sql.Identifier(database),
                                                                                    sql.Identifier(user)))

def create_user(
    connection: psycopg2.extensions.connection, user: str, password: str, admin: bool = False
) -> None:
    """Creates a database user.

    Args:
        connection: psycopg2 connection object.
        user: user to be created.
        password: password to be assigned to the user.
        admin: whether the user should have additional admin privileges.
    """
    user_definition = f"{user} WITH LOGIN{' SUPERUSER' if admin else ''} ENCRYPTED PASSWORD '{password}'"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT TRUE FROM pg_roles WHERE rolname='{user}';")
        # logger.error(cursor.fetchone())
        if cursor.fetchone() is not None:
            cursor.execute(f"ALTER ROLE {user_definition};")
        else:
            cursor.execute(f"CREATE ROLE {user_definition};")


def drop_user(
    connection: psycopg2.extensions.connection, user: str
) -> None:
    """Drops a database user.

    Args:
        connection: psycopg2 connection object.
        user: user to be dropped.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"REASSIGN OWNED BY {user} TO postgres;")
        cursor.execute(f"DROP OWNED BY {user};")
        cursor.execute(f"DROP ROLE {user};")
