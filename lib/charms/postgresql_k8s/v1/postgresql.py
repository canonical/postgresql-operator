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
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Tuple

import psycopg2
from ops.model import Relation
from psycopg2.sql import SQL, Composed, Identifier, Literal

# The unique Charmhub library identifier, never change it
LIBID = "24ee217a54e840a598ff21a079c3e678"

# Increment this major API version when introducing breaking changes
LIBAPI = 1

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 0

# Groups to distinguish HBA access
ACCESS_GROUP_IDENTITY = "identity_access"
ACCESS_GROUP_INTERNAL = "internal_access"
ACCESS_GROUP_RELATION = "relation_access"

# List of access groups to filter role assignments by
ACCESS_GROUPS = [
    ACCESS_GROUP_IDENTITY,
    ACCESS_GROUP_INTERNAL,
    ACCESS_GROUP_RELATION,
]

ROLE_STATS = "charmed_stats"
ROLE_READ = "charmed_read"
ROLE_DML = "charmed_dml"
ROLE_BACKUP = "charmed_backup"
ROLE_DBA = "charmed_dba"

# Groups to distinguish database permissions
PERMISSIONS_GROUP_ADMIN = "admin"

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


class PostgreSQLAssignGroupError(Exception):
    """Exception raised when assigning to a group fails."""


class PostgreSQLCreateDatabaseError(Exception):
    """Exception raised when creating a database fails."""


class PostgreSQLCreateGroupError(Exception):
    """Exception raised when creating a group fails."""


class PostgreSQLCreateUserError(Exception):
    """Exception raised when creating a user fails."""

    def __init__(self, message: Optional[str] = None):
        super().__init__(message)
        self.message = message


class PostgreSQLDatabasesSetupError(Exception):
    """Exception raised when the databases setup fails."""


class PostgreSQLDeleteUserError(Exception):
    """Exception raised when deleting a user fails."""


class PostgreSQLEnableDisableExtensionError(Exception):
    """Exception raised when enabling/disabling an extension fails."""


class PostgreSQLGetLastArchivedWALError(Exception):
    """Exception raised when retrieving last archived WAL fails."""


class PostgreSQLGetCurrentTimelineError(Exception):
    """Exception raised when retrieving current timeline id for the PostgreSQL unit fails."""


class PostgreSQLGetPostgreSQLVersionError(Exception):
    """Exception raised when retrieving PostgreSQL version fails."""


class PostgreSQLListGroupsError(Exception):
    """Exception raised when retrieving PostgreSQL groups list fails."""


class PostgreSQLListUsersError(Exception):
    """Exception raised when retrieving PostgreSQL users list fails."""


class PostgreSQLUpdateUserPasswordError(Exception):
    """Exception raised when updating a user password fails."""


class PostgreSQLCreatePredefinedRolesError(Exception):
    """Exception raised when creating predefined roles."""


class PostgreSQLGrantDatabasePrivilegesToUserError(Exception):
    """Exception raised when granting database privileges to user."""


class PostgreSQL:
    """Class to encapsulate all operations related to interacting with PostgreSQL instance."""

    def __init__(
        self,
        primary_host: str,
        current_host: str,
        user: str,
        password: str,
        database: str,
        system_users: Optional[List[str]] = None,
    ):
        self.primary_host = primary_host
        self.current_host = current_host
        self.user = user
        self.password = password
        self.database = database
        self.system_users = system_users if system_users else []

    def _configure_pgaudit(self, enable: bool) -> None:
        connection = None
        try:
            connection = self._connect_to_database()
            connection.autocommit = True
            with connection.cursor() as cursor:
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
        connection = psycopg2.connect(
            f"dbname='{database if database else self.database}' user='{self.user}' host='{host}'"
            f"password='{self.password}' connect_timeout=1"
        )
        connection.autocommit = True
        return connection

    def create_access_groups(self) -> None:
        """Create access groups to distinguish HBA authentication methods."""
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                for group in ACCESS_GROUPS:
                    cursor.execute(
                        SQL("CREATE ROLE {} NOLOGIN;").format(
                            Identifier(group),
                        )
                    )
        except psycopg2.Error as e:
            logger.error(f"Failed to create access groups: {e}")
            raise PostgreSQLCreateGroupError() from e
        finally:
            if connection is not None:
                connection.close()

    def create_database(
        self,
        database: str,
        user: str,
        plugins: Optional[List[str]] = None,
        client_relations: Optional[List[Relation]] = None,
    ) -> None:
        """Creates a new database and grant privileges to a user on it.

        Args:
            database: database to be created.
            user: user that will have access to the database.
            plugins: extensions to enable in the new database.
            client_relations: current established client relations.
        """
        plugins = plugins if plugins else []
        client_relations = client_relations if client_relations else []
        try:
            connection = self._connect_to_database()
            cursor = connection.cursor()
            cursor.execute(
                SQL("SELECT datname FROM pg_database WHERE datname={};").format(Literal(database))
            )
            if cursor.fetchone() is None:
                cursor.execute(SQL("CREATE DATABASE {};").format(Identifier(database)))
            cursor.execute(
                SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC;").format(
                    Identifier(database)
                )
            )
            for user_to_grant_access in [user, PERMISSIONS_GROUP_ADMIN, *self.system_users]:
                cursor.execute(
                    SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {};").format(
                        Identifier(database), Identifier(user_to_grant_access)
                    )
                )
            relations_accessing_this_database = 0
            for relation in client_relations:
                for data in relation.data.values():
                    if data.get("database") == database:
                        relations_accessing_this_database += 1
            with self._connect_to_database(database=database) as conn, conn.cursor() as curs:
                curs.execute(
                    "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' and schema_name <> 'information_schema';"
                )
                schemas = [row[0] for row in curs.fetchall()]
                statements = self._generate_database_privileges_statements(
                    relations_accessing_this_database, schemas, user
                )
                for statement in statements:
                    curs.execute(statement)
        except psycopg2.Error as e:
            logger.error(f"Failed to create database: {e}")
            raise PostgreSQLCreateDatabaseError() from e

        # Enable preset extensions
        self.enable_disable_extensions(dict.fromkeys(plugins, True), database)

    def create_user(
        self,
        user: str,
        password: Optional[str] = None,
        admin: bool = False,
        extra_user_roles: Optional[List[str]] = None,
    ) -> None:
        """Creates a database user.

        Args:
            user: user to be created.
            password: password to be assigned to the user.
            admin: whether the user should have additional admin privileges.
            extra_user_roles: additional privileges and/or roles to be assigned to the user.
        """
        try:
            # Separate roles and privileges from the provided extra user roles.
            admin_role = False
            roles = privileges = None
            if extra_user_roles:
                admin_role = PERMISSIONS_GROUP_ADMIN in extra_user_roles
                valid_privileges, valid_roles = self.list_valid_privileges_and_roles()
                roles = [
                    role
                    for role in extra_user_roles
                    if role in valid_roles and role != PERMISSIONS_GROUP_ADMIN
                ]
                privileges = {
                    extra_user_role
                    for extra_user_role in extra_user_roles
                    if extra_user_role not in roles and extra_user_role != PERMISSIONS_GROUP_ADMIN
                }
                invalid_privileges = [
                    privilege for privilege in privileges if privilege not in valid_privileges
                ]
                if len(invalid_privileges) > 0:
                    logger.error(f"Invalid extra user roles: {', '.join(privileges)}")
                    raise PostgreSQLCreateUserError(INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE)

            with self._connect_to_database() as connection, connection.cursor() as cursor:
                # Create or update the user.
                cursor.execute(
                    SQL("SELECT TRUE FROM pg_roles WHERE rolname={};").format(Literal(user))
                )
                if cursor.fetchone() is not None:
                    user_definition = "ALTER ROLE {}"
                else:
                    user_definition = "CREATE ROLE {}"
                user_definition += f"WITH {'NOLOGIN' if user == 'admin' else 'LOGIN'}{' SUPERUSER' if admin else ''} ENCRYPTED PASSWORD '{password}'{'IN ROLE admin CREATEDB' if admin_role else ''}"
                if privileges:
                    user_definition += f" {' '.join(privileges)}"
                cursor.execute(SQL("BEGIN;"))
                cursor.execute(SQL("SET LOCAL log_statement = 'none';"))
                cursor.execute(SQL(f"{user_definition};").format(Identifier(user)))
                cursor.execute(SQL("COMMIT;"))

                # Add extra user roles to the new user.
                if roles:
                    for role in roles:
                        cursor.execute(
                            SQL("GRANT {} TO {};").format(Identifier(role), Identifier(user))
                        )
        except psycopg2.Error as e:
            logger.error(f"Failed to create user: {e}")
            raise PostgreSQLCreateUserError() from e

    def create_predefined_roles(self) -> None:
        """Create predefined roles."""
        connection = None
        try:
            for database in ["postgres", "template1"]:
                with self._connect_to_database(
                    database=database,
                ) as connection, connection.cursor() as cursor:
                    cursor.execute(SQL("CREATE EXTENSION IF NOT EXISTS set_user;"))
        finally:
            if connection is not None:
                connection.close()
        role_to_queries = {
            ROLE_STATS: [
                f"CREATE ROLE {ROLE_STATS} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_monitor",
            ],
            ROLE_READ: [
                f"CREATE ROLE {ROLE_READ} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_read_all_data",
            ],
            ROLE_DML: [
                f"CREATE ROLE {ROLE_DML} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_write_all_data",
            ],
            ROLE_BACKUP: [
                f"CREATE ROLE {ROLE_BACKUP} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_checkpoint",
                f"GRANT {ROLE_STATS} TO {ROLE_BACKUP}",
                f"GRANT execute ON FUNCTION pg_backup_start TO {ROLE_BACKUP}",
                f"GRANT execute ON FUNCTION pg_backup_stop TO {ROLE_BACKUP}",
                f"GRANT execute ON FUNCTION pg_create_restore_point TO {ROLE_BACKUP}",
                f"GRANT execute ON FUNCTION pg_switch_wal TO {ROLE_BACKUP}",
            ],
            ROLE_DBA: [
                f"CREATE ROLE {ROLE_DBA} NOSUPERUSER CREATEDB NOCREATEROLE NOLOGIN NOREPLICATION;",
                f"GRANT execute ON FUNCTION set_user(text) TO {ROLE_DBA};",
                f"GRANT execute ON FUNCTION set_user(text, text) TO {ROLE_DBA};",
                f"GRANT execute ON FUNCTION set_user_u(text) TO {ROLE_DBA};"
                f"GRANT execute ON FUNCTION reset_user() TO {ROLE_DBA};"
                f"GRANT execute ON FUNCTION reset_user(text) TO {ROLE_DBA};"
            ]
        }

        _, existing_roles = self.list_valid_privileges_and_roles()

        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                for role, queries in role_to_queries.items():
                    if role in existing_roles:
                        logger.debug(f"Role {role} already exists")
                        continue

                    logger.info(f"Creating predefined role {role}")

                    for query in queries:
                        cursor.execute(SQL(query))
        except psycopg2.Error as e:
            logger.error(f"Failed to create predefined roles: {e}")
            raise PostgreSQLCreatePredefinedRolesError() from e
        finally:
            if connection is not None:
                connection.close()

    def grant_database_privileges_to_user(
        self, user: str, database: str, privileges: list[str]
    ) -> None:
        """Grant the specified privileges on the provided database for the user."""
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("GRANT {} ON DATABASE {} TO {};").format(
                        Identifier(", ".join(privileges)), Identifier(database), Identifier(user)
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to grant privileges to user: {e}")
            raise PostgreSQLGrantDatabasePrivilegesToUserError() from e

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
                        SQL("REASSIGN OWNED BY {} TO {};").format(
                            Identifier(user), Identifier(self.user)
                        )
                    )
                    cursor.execute(SQL("DROP OWNED BY {};").format(Identifier(user)))

            # Delete the user.
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(SQL("DROP ROLE {};").format(Identifier(user)))
        except psycopg2.Error as e:
            logger.error(f"Failed to delete user: {e}")
            raise PostgreSQLDeleteUserError() from e

    def grant_internal_access_group_memberships(self) -> None:
        """Grant membership to the internal access-group to existing internal users."""
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                for user in self.system_users:
                    cursor.execute(
                        SQL("GRANT {} TO {};").format(
                            Identifier(ACCESS_GROUP_INTERNAL),
                            Identifier(user),
                        )
                    )
        except psycopg2.Error as e:
            logger.error(f"Failed to grant internal access group memberships: {e}")
            raise PostgreSQLAssignGroupError() from e
        finally:
            if connection is not None:
                connection.close()

    def grant_relation_access_group_memberships(self) -> None:
        """Grant membership to the relation access-group to existing relation users."""
        rel_users = self.list_users_from_relation()
        if not rel_users:
            return

        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                rel_groups = SQL(",").join(Identifier(group) for group in [ACCESS_GROUP_RELATION])
                rel_users = SQL(",").join(Identifier(user) for user in rel_users)

                cursor.execute(
                    SQL("GRANT {groups} TO {users};").format(
                        groups=rel_groups,
                        users=rel_users,
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to grant relation access group memberships: {e}")
            raise PostgreSQLAssignGroupError() from e
        finally:
            if connection is not None:
                connection.close()

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
        except psycopg2.errors.UniqueViolation:
            pass
        except psycopg2.errors.DependentObjectsStillExist:
            raise
        except psycopg2.Error as e:
            raise PostgreSQLEnableDisableExtensionError() from e
        finally:
            if connection is not None:
                connection.close()

    def _generate_database_privileges_statements(
        self, relations_accessing_this_database: int, schemas: List[str], user: str
    ) -> List[Composed]:
        """Generates a list of databases privileges statements."""
        statements = []
        if relations_accessing_this_database == 1:
            statements.append(
                SQL(
                    """DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN (SELECT statement FROM (SELECT 1 AS index,'ALTER TABLE '|| schemaname || '."' || tablename ||'" OWNER TO {};' AS statement
FROM pg_tables WHERE NOT schemaname IN ('pg_catalog', 'information_schema')
UNION SELECT 2 AS index,'ALTER SEQUENCE '|| sequence_schema || '."' || sequence_name ||'" OWNER TO {};' AS statement
FROM information_schema.sequences WHERE NOT sequence_schema IN ('pg_catalog', 'information_schema')
UNION SELECT 3 AS index,'ALTER FUNCTION '|| nsp.nspname || '."' || p.proname ||'"('||pg_get_function_identity_arguments(p.oid)||') OWNER TO {};' AS statement
FROM pg_proc p JOIN pg_namespace nsp ON p.pronamespace = nsp.oid WHERE NOT nsp.nspname IN ('pg_catalog', 'information_schema') AND p.prokind = 'f'
UNION SELECT 4 AS index,'ALTER PROCEDURE '|| nsp.nspname || '."' || p.proname ||'"('||pg_get_function_identity_arguments(p.oid)||') OWNER TO {};' AS statement
FROM pg_proc p JOIN pg_namespace nsp ON p.pronamespace = nsp.oid WHERE NOT nsp.nspname IN ('pg_catalog', 'information_schema') AND p.prokind = 'p'
UNION SELECT 5 AS index,'ALTER AGGREGATE '|| nsp.nspname || '."' || p.proname ||'"('||pg_get_function_identity_arguments(p.oid)||') OWNER TO {};' AS statement
FROM pg_proc p JOIN pg_namespace nsp ON p.pronamespace = nsp.oid WHERE NOT nsp.nspname IN ('pg_catalog', 'information_schema') AND p.prokind = 'a'
UNION SELECT 6 AS index,'ALTER VIEW '|| schemaname || '."' || viewname ||'" OWNER TO {};' AS statement
FROM pg_catalog.pg_views WHERE NOT schemaname IN ('pg_catalog', 'information_schema')) AS statements ORDER BY index) LOOP
      EXECUTE format(r.statement);
  END LOOP;
END; $$;"""
                ).format(
                    Identifier(user),
                    Identifier(user),
                    Identifier(user),
                    Identifier(user),
                    Identifier(user),
                    Identifier(user),
                )
            )
            statements.append(
                SQL(
                    "UPDATE pg_catalog.pg_largeobject_metadata\n"
                    "SET lomowner = (SELECT oid FROM pg_roles WHERE rolname = {})\n"
                    "WHERE lomowner = (SELECT oid FROM pg_roles WHERE rolname = {});"
                ).format(Literal(user), Literal(self.user))
            )
            for schema in schemas:
                statements.append(
                    SQL("ALTER SCHEMA {} OWNER TO {};").format(
                        Identifier(schema), Identifier(user)
                    )
                )
        else:
            for schema in schemas:
                schema = Identifier(schema)
                statements.extend([
                    SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} TO {};").format(
                        schema, Identifier(user)
                    ),
                    SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} TO {};").format(
                        schema, Identifier(user)
                    ),
                    SQL("GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA {} TO {};").format(
                        schema, Identifier(user)
                    ),
                    SQL("GRANT USAGE ON SCHEMA {} TO {};").format(schema, Identifier(user)),
                    SQL("GRANT CREATE ON SCHEMA {} TO {};").format(schema, Identifier(user)),
                ])
        return statements

    def get_last_archived_wal(self) -> str:
        """Get the name of the last archived wal for the current PostgreSQL cluster."""
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT last_archived_wal FROM pg_stat_archiver;")
                return cursor.fetchone()[0]
        except psycopg2.Error as e:
            logger.error(f"Failed to get PostgreSQL last archived WAL: {e}")
            raise PostgreSQLGetLastArchivedWALError() from e

    def get_current_timeline(self) -> str:
        """Get the timeline id for the current PostgreSQL unit."""
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT timeline_id FROM pg_control_checkpoint();")
                return cursor.fetchone()[0]
        except psycopg2.Error as e:
            logger.error(f"Failed to get PostgreSQL current timeline id: {e}")
            raise PostgreSQLGetCurrentTimelineError() from e

    def get_postgresql_text_search_configs(self) -> Set[str]:
        """Returns the PostgreSQL available text search configs.

        Returns:
            Set of PostgreSQL text search configs.
        """
        with self._connect_to_database(
            database_host=self.current_host
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT CONCAT('pg_catalog.', cfgname) FROM pg_ts_config;")
            text_search_configs = cursor.fetchall()
            return {text_search_config[0] for text_search_config in text_search_configs}

    def get_postgresql_timezones(self) -> Set[str]:
        """Returns the PostgreSQL available timezones.

        Returns:
            Set of PostgreSQL timezones.
        """
        with self._connect_to_database(
            database_host=self.current_host
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT name FROM pg_timezone_names;")
            timezones = cursor.fetchall()
            return {timezone[0] for timezone in timezones}

    def get_postgresql_default_table_access_methods(self) -> Set[str]:
        """Returns the PostgreSQL available table access methods.

        Returns:
            Set of PostgreSQL table access methods.
        """
        with self._connect_to_database(
            database_host=self.current_host
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT amname FROM pg_am WHERE amtype = 't';")
            access_methods = cursor.fetchall()
            return {access_method[0] for access_method in access_methods}

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
                # Split to get only the version number.
                return cursor.fetchone()[0].split(" ")[1]
        except psycopg2.Error as e:
            logger.error(f"Failed to get PostgreSQL version: {e}")
            raise PostgreSQLGetPostgreSQLVersionError() from e

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
                database_host=self.current_host if check_current_host else None
            ) as connection, connection.cursor() as cursor:
                cursor.execute("SHOW ssl;")
                return "on" in cursor.fetchone()[0]
        except psycopg2.Error:
            # Connection errors happen when PostgreSQL has not started yet.
            return False

    def list_access_groups(self) -> Set[str]:
        """Returns the list of PostgreSQL database access groups.

        Returns:
            List of PostgreSQL database access groups.
        """
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT groname FROM pg_catalog.pg_group WHERE groname LIKE '%_access';"
                )
                access_groups = cursor.fetchall()
                return {group[0] for group in access_groups}
        except psycopg2.Error as e:
            logger.error(f"Failed to list PostgreSQL database access groups: {e}")
            raise PostgreSQLListGroupsError() from e
        finally:
            if connection is not None:
                connection.close()

    def list_users(self) -> Set[str]:
        """Returns the list of PostgreSQL database users.

        Returns:
            List of PostgreSQL database users.
        """
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT usename FROM pg_catalog.pg_user;")
                usernames = cursor.fetchall()
                return {username[0] for username in usernames}
        except psycopg2.Error as e:
            logger.error(f"Failed to list PostgreSQL database users: {e}")
            raise PostgreSQLListUsersError() from e
        finally:
            if connection is not None:
                connection.close()

    def list_users_from_relation(self) -> Set[str]:
        """Returns the list of PostgreSQL database users that were created by a relation.

        Returns:
            List of PostgreSQL database users.
        """
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT usename "
                    "FROM pg_catalog.pg_user "
                    "WHERE usename LIKE 'relation_id_%' OR usename LIKE 'relation-%';"
                )
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
        with self._connect_to_database() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT rolname FROM pg_roles;")
            return {
                "createdb",
                "createrole",
                "superuser",
            }, {role[0] for role in cursor.fetchall() if role[0]}

    def set_up_database(self, temp_location: Optional[str] = None) -> None:
        """Set up postgres database with the right permissions."""
        connection = None
        cursor = None
        try:
            connection = self._connect_to_database()
            cursor = connection.cursor()

            if temp_location is not None:
                cursor.execute("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
                if cursor.fetchone() is None:
                    cursor.execute(f"CREATE TABLESPACE temp LOCATION '{temp_location}';")
                    cursor.execute("GRANT CREATE ON TABLESPACE temp TO public;")

            cursor.execute("SELECT TRUE FROM pg_roles WHERE rolname='admin';")
            if cursor.fetchone() is None:
                # Allow access to the postgres database only to the system users.
                cursor.execute("REVOKE ALL PRIVILEGES ON DATABASE postgres FROM PUBLIC;")
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC;")
                for user in self.system_users:
                    cursor.execute(
                        SQL("GRANT ALL PRIVILEGES ON DATABASE postgres TO {};").format(
                            Identifier(user)
                        )
                    )
                self.create_user(
                    PERMISSIONS_GROUP_ADMIN,
                    extra_user_roles=[ROLE_READ, ROLE_DML],
                )
                cursor.execute("GRANT CONNECT ON DATABASE postgres TO admin;")
        except psycopg2.Error as e:
            logger.error(f"Failed to set up databases: {e}")
            raise PostgreSQLDatabasesSetupError() from e
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def update_user_password(
        self, username: str, password: str, database_host: Optional[str] = None
    ) -> None:
        """Update a user password.

        Args:
            username: the user to update the password.
            password: the new password for the user.
            database_host: the host to connect to.

        Raises:
            PostgreSQLUpdateUserPasswordError if the password couldn't be changed.
        """
        connection = None
        try:
            with self._connect_to_database(
                database_host=database_host
            ) as connection, connection.cursor() as cursor:
                cursor.execute(SQL("BEGIN;"))
                cursor.execute(SQL("SET LOCAL log_statement = 'none';"))
                cursor.execute(
                    SQL("ALTER USER {} WITH ENCRYPTED PASSWORD '" + password + "';").format(
                        Identifier(username)
                    )
                )
                cursor.execute(SQL("COMMIT;"))
        except psycopg2.Error as e:
            logger.error(f"Failed to update user password: {e}")
            raise PostgreSQLUpdateUserPasswordError() from e
        finally:
            if connection is not None:
                connection.close()

    def is_restart_pending(self) -> bool:
        """Query pg_settings for pending restart."""
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM pg_settings WHERE pending_restart=True;")
                return cursor.fetchone()[0] > 0
        except psycopg2.OperationalError:
            logger.warning("Failed to connect to PostgreSQL.")
            return False
        except psycopg2.Error as e:
            logger.error(f"Failed to check if restart is pending: {e}")
            return False
        finally:
            if connection:
                connection.close()

    @staticmethod
    def build_postgresql_group_map(group_map: Optional[str]) -> List[Tuple]:
        """Build the PostgreSQL authorization group-map.

        Args:
            group_map: serialized group-map with the following format:
                <ldap_group_1>=<psql_group_1>,
                <ldap_group_2>=<psql_group_2>,
                ...

        Returns:
            List of LDAP group to PostgreSQL group tuples.
        """
        if group_map is None:
            return []

        group_mappings = group_map.split(",")
        group_mappings = (mapping.strip() for mapping in group_mappings)
        group_map_list = []

        for mapping in group_mappings:
            mapping_parts = mapping.split("=")
            if len(mapping_parts) != 2:
                raise ValueError("The group-map must contain value pairs split by commas")

            ldap_group = mapping_parts[0]
            psql_group = mapping_parts[1]

            if psql_group in [*ACCESS_GROUPS, PERMISSIONS_GROUP_ADMIN]:
                logger.warning(f"Tried to assign LDAP users to forbidden group: {psql_group}")
                continue

            group_map_list.append((ldap_group, psql_group))

        return group_map_list

    @staticmethod
    def build_postgresql_parameters(
        config_options: dict, available_memory: int, limit_memory: Optional[int] = None
    ) -> Optional[dict]:
        """Builds the PostgreSQL parameters.

        Args:
            config_options: charm config options containing profile and PostgreSQL parameters.
            available_memory: available memory to use in calculation in bytes.
            limit_memory: (optional) limit memory to use in calculation in bytes.

        Returns:
            Dictionary with the PostgreSQL parameters.
        """
        if limit_memory:
            available_memory = min(available_memory, limit_memory)
        profile = config_options["profile"]
        logger.debug(f"Building PostgreSQL parameters for {profile=} and {available_memory=}")
        parameters = {}
        for config, value in config_options.items():
            # Filter config option not related to PostgreSQL parameters.
            if not config.startswith((
                "connection",
                "cpu",
                "durability",
                "instance",
                "logging",
                "memory",
                "optimizer",
                "request",
                "response",
                "session",
                "storage",
                "vacuum",
            )):
                continue
            parameter = "_".join(config.split("_")[1:])
            if parameter in ["date_style", "time_zone"]:
                parameter = "".join(x.capitalize() for x in parameter.split("_"))
            parameters[parameter] = value
        shared_buffers_max_value_in_mb = int(available_memory * 0.4 / 10**6)
        shared_buffers_max_value = int(shared_buffers_max_value_in_mb * 10**3 / 8)
        if parameters.get("shared_buffers", 0) > shared_buffers_max_value:
            raise Exception(
                f"Shared buffers config option should be at most 40% of the available memory, which is {shared_buffers_max_value_in_mb}MB"
            )
        if profile == "production":
            if "shared_buffers" in parameters:
                # Convert to bytes to use in the calculation.
                shared_buffers = parameters["shared_buffers"] * 8 * 10**3
            else:
                # Use 25% of the available memory for shared_buffers.
                # and the remaining as cache memory.
                shared_buffers = int(available_memory * 0.25)
                parameters["shared_buffers"] = f"{int(shared_buffers * 128 / 10**6)}"
            effective_cache_size = int(available_memory - shared_buffers)
            parameters.update({
                "effective_cache_size": f"{int(effective_cache_size / 10**6) * 128}"
            })
        return parameters

    def validate_date_style(self, date_style: str) -> bool:
        """Validate a date style against PostgreSQL.

        Returns:
            Whether the date style is valid.
        """
        try:
            with self._connect_to_database(
                database_host=self.current_host
            ) as connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "SET DateStyle to {};",
                    ).format(Identifier(date_style))
                )
            return True
        except psycopg2.Error:
            return False

    def validate_group_map(self, group_map: Optional[str]) -> bool:
        """Validate the PostgreSQL authorization group-map.

        Args:
            group_map: serialized group-map with the following format:
                <ldap_group_1>=<psql_group_1>,
                <ldap_group_2>=<psql_group_2>,
                ...

        Returns:
            Whether the group-map is valid.
        """
        if group_map is None:
            return True

        try:
            group_map = self.build_postgresql_group_map(group_map)
        except ValueError:
            return False

        for _, psql_group in group_map:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                query = SQL("SELECT TRUE FROM pg_roles WHERE rolname={};")
                query = query.format(Literal(psql_group))
                cursor.execute(query)

                if cursor.fetchone() is None:
                    return False

        return True
