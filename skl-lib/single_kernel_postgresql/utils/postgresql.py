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
import os
import pwd
from datetime import UTC, datetime

import psycopg2
from ops import ConfigData
from psycopg2.sql import SQL, Identifier, Literal

from ..compat.postgresql import (
    ACCESS_GROUP_RELATION,
    ALLOWED_ROLES,
    INVALID_DATABASE_NAME_BLOCKING_MESSAGE,  # noqa: F401
    INVALID_DATABASE_NAMES,  # noqa: F401
    INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,  # noqa: F401
    REQUIRED_PLUGINS,  # noqa: F401
    ROLE_ADMIN,
    ROLE_BACKUP,
    ROLE_DATABASES_OWNER,
    ROLE_DBA,
    ROLE_DML,
    ROLE_READ,
    ROLE_STATS,
    PostgreSQLBase,
    PostgreSQLBaseError,
    PostgreSQLCreateDatabaseError,  # noqa: F401
    PostgreSQLCreateUserError,  # noqa: F401
    PostgreSQLDeleteUserError,  # noqa: F401
    PostgreSQLEnableDisableExtensionError,  # noqa: F401
    PostgreSQLGetPostgreSQLVersionError,  # noqa: F401
    PostgreSQLListUsersError,
    PostgreSQLUndefinedHostError,  # noqa: F401
    PostgreSQLUndefinedPasswordError,  # noqa: F401
)
from ..config.enums import Substrates
from ..config.literals import (
    POSTGRESQL_STORAGE_PERMISSIONS,
    SNAP_USER,
)
from .filesystem import change_owner, is_tmpfs

# Groups to distinguish HBA access
ACCESS_GROUP_IDENTITY = "identity_access"
ACCESS_GROUP_INTERNAL = "internal_access"

# List of access groups to filter role assignments by
ACCESS_GROUPS = [
    ACCESS_GROUP_IDENTITY,
    ACCESS_GROUP_INTERNAL,
    ACCESS_GROUP_RELATION,
]

logger = logging.getLogger(__name__)


class PostgreSQLAssignGroupError(PostgreSQLBaseError):
    """Exception raised when assigning to a group fails."""


class PostgreSQLCreateGroupError(PostgreSQLBaseError):
    """Exception raised when creating a group fails."""


class PostgreSQLUpdateUserError(PostgreSQLBaseError):
    """Exception raised when creating a user fails."""


class PostgreSQLDatabasesSetupError(PostgreSQLBaseError):
    """Exception raised when the databases setup fails."""


class PostgreSQLGetLastArchivedWALError(PostgreSQLBaseError):
    """Exception raised when retrieving last archived WAL fails."""


class PostgreSQLGetCurrentTimelineError(PostgreSQLBaseError):
    """Exception raised when retrieving current timeline id for the PostgreSQL unit fails."""


class PostgreSQLListDatabasesError(PostgreSQLBaseError):
    """Exception raised when retrieving the databases."""


class PostgreSQLListAccessibleDatabasesForUserError(PostgreSQLBaseError):
    """Exception raised when retrieving the accessible databases for a user fails."""


class PostgreSQLListGroupsError(PostgreSQLBaseError):
    """Exception raised when retrieving PostgreSQL groups list fails."""


class PostgreSQLUpdateUserPasswordError(PostgreSQLBaseError):
    """Exception raised when updating a user password fails."""


class PostgreSQLCreatePredefinedRolesError(PostgreSQLBaseError):
    """Exception raised when creating predefined roles."""


class PostgreSQLDatabaseExistsError(PostgreSQLBaseError):
    """Exception raised during database existence check."""


class PostgreSQLTableExistsError(PostgreSQLBaseError):
    """Exception raised during table existence check."""


class PostgreSQLIsTableEmptyError(PostgreSQLBaseError):
    """Exception raised during table emptiness check."""


class PostgreSQLCreatePublicationError(PostgreSQLBaseError):
    """Exception raised when creating PostgreSQL publication."""


class PostgreSQLPublicationExistsError(PostgreSQLBaseError):
    """Exception raised during PostgreSQL publication existence check."""


class PostgreSQLAlterPublicationError(PostgreSQLBaseError):
    """Exception raised when altering PostgreSQL publication."""


class PostgreSQLDropPublicationError(PostgreSQLBaseError):
    """Exception raised when dropping PostgreSQL publication."""


class PostgreSQLCreateSubscriptionError(PostgreSQLBaseError):
    """Exception raised when creating PostgreSQL subscription."""


class PostgreSQLSubscriptionExistsError(PostgreSQLBaseError):
    """Exception raised during PostgreSQL subscription existence check."""


class PostgreSQLUpdateSubscriptionError(PostgreSQLBaseError):
    """Exception raised when updating PostgreSQL subscription."""


class PostgreSQLRefreshSubscriptionError(PostgreSQLBaseError):
    """Exception raised when refreshing PostgreSQL subscription."""


class PostgreSQLDropSubscriptionError(PostgreSQLBaseError):
    """Exception raised when dropping PostgreSQL subscription."""


class PostgreSQLGrantDatabasePrivilegesToUserError(PostgreSQLBaseError):
    """Exception raised when granting database privileges to user."""


class PostgreSQL(PostgreSQLBase):
    """Class to encapsulate all operations related to interacting with PostgreSQL instance."""

    def __init__(
        self,
        substrate: Substrates,
        primary_host: str | None,
        current_host: str | None,
        user: str,
        password: str | None,
        database: str,
        system_users: list[str] | None = None,
    ):
        """Create a PostgreSQL helper.

        Args:
            substrate: substrate where the charm is running (Substrates.K8S or Substrates.VM).
            primary_host: hostname or address for primary database host.
            current_host: hostname or address for the current database host.
            user: username to connect as.
            password: password for the user.
            database: default database name.
            system_users: list of system users.
        """
        super().__init__(primary_host, current_host, user, password, database)
        self.substrate = substrate
        self.system_users = system_users if system_users else []

    def create_access_groups(self) -> None:
        """Create access groups to distinguish HBA authentication methods."""
        connection = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                for group in ACCESS_GROUPS:
                    cursor.execute(
                        SQL("SELECT TRUE FROM pg_roles WHERE rolname={};").format(Literal(group))
                    )
                    if cursor.fetchone() is not None:
                        continue
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

    def create_predefined_instance_roles(self) -> None:
        """Create predefined instance roles."""
        connection = None
        try:
            for database in self._get_existing_databases():
                with (
                    self._connect_to_database(
                        database=database,
                    ) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute(SQL("CREATE EXTENSION IF NOT EXISTS set_user;"))
        finally:
            if connection is not None:
                connection.close()
            connection = None

        role_to_queries = {
            ROLE_STATS: [
                f"CREATE ROLE {ROLE_STATS} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_monitor",
            ],
            ROLE_READ: [
                f"CREATE ROLE {ROLE_READ} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_read_all_data, {ROLE_STATS}",
            ],
            ROLE_DML: [
                f"CREATE ROLE {ROLE_DML} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE pg_write_all_data, {ROLE_READ}",
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
                f"CREATE ROLE {ROLE_DBA} NOSUPERUSER CREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE {ROLE_DML};"
            ],
            ROLE_ADMIN: [
                f"CREATE ROLE {ROLE_ADMIN} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOLOGIN IN ROLE {ROLE_DML}",
            ],
        }

        try:
            for database in ["postgres", "template1"]:
                with (
                    self._connect_to_database(
                        database=database,
                    ) as connection,
                    connection.cursor() as cursor,
                ):
                    existing_roles = self.list_existing_roles()
                    for role, queries in role_to_queries.items():
                        for index, query in enumerate(queries):
                            if index == 0:
                                if role in existing_roles:
                                    logger.debug(f"Role {role} already exists")
                                    continue
                                else:
                                    logger.info(f"Creating predefined role {role}")
                            cursor.execute(SQL(query))
        except psycopg2.Error as e:
            logger.error(f"Failed to create predefined instance roles: {e}")
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

    def grant_replication_privileges(
        self,
        user: str,
        database: str,
        schematables: list[str],
        old_schematables: list[str] | None = None,
    ) -> None:
        """Grant CONNECT privilege on database and SELECT privilege on tables.

        Args:
            user: target user for privileges grant.
            database: database to grant CONNECT privilege on.
            schematables: list of tables with schema notation to grant SELECT privileges on.
            old_schematables: list of tables with schema notation to revoke all privileges from.
        """
        connection = None
        try:
            connection = self._connect_to_database(database=database)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("GRANT CONNECT ON DATABASE {} TO {};").format(
                        Identifier(database), Identifier(user)
                    )
                )
                if old_schematables:
                    cursor.execute(
                        SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM {};").format(
                            SQL(",").join(
                                Identifier(schematable.split(".")[0], schematable.split(".")[1])
                                for schematable in old_schematables
                            ),
                            Identifier(user),
                        )
                    )
                cursor.execute(
                    SQL("GRANT SELECT ON TABLE {} TO {};").format(
                        SQL(",").join(
                            Identifier(schematable.split(".")[0], schematable.split(".")[1])
                            for schematable in schematables
                        ),
                        Identifier(user),
                    )
                )
        finally:
            if connection:
                connection.close()

    def revoke_replication_privileges(
        self, user: str, database: str, schematables: list[str]
    ) -> None:
        """Revoke all privileges from tables and database.

        Args:
            user: target user for privileges revocation.
            database: database to remove all privileges from.
            schematables: list of tables with schema notation to revoke all privileges from.
        """
        connection = None
        try:
            connection = self._connect_to_database(database=database)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM {};").format(
                        SQL(",").join(
                            Identifier(schematable.split(".")[0], schematable.split(".")[1])
                            for schematable in schematables
                        ),
                        Identifier(user),
                    )
                )
                cursor.execute(
                    SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {};").format(
                        Identifier(database), Identifier(user)
                    )
                )
        finally:
            if connection:
                connection.close()

    def get_last_archived_wal(self) -> str:
        """Get the name of the last archived wal for the current PostgreSQL cluster."""
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                # Should always be present
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
                # There should always be a timeline
                return cursor.fetchone()[0]
        except psycopg2.Error as e:
            logger.error(f"Failed to get PostgreSQL current timeline id: {e}")
            raise PostgreSQLGetCurrentTimelineError() from e

    def get_postgresql_text_search_configs(self) -> set[str]:
        """Returns the PostgreSQL available text search configs.

        Returns:
            Set of PostgreSQL text search configs.
        """
        with (
            self._connect_to_database(database_host=self.current_host) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT CONCAT('pg_catalog.', cfgname) FROM pg_ts_config;")
            text_search_configs = cursor.fetchall()
            return {text_search_config[0] for text_search_config in text_search_configs}

    def get_postgresql_timezones(self) -> set[str]:
        """Returns the PostgreSQL available timezones.

        Returns:
            Set of PostgreSQL timezones.
        """
        with (
            self._connect_to_database(database_host=self.current_host) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT name FROM pg_timezone_names;")
            timezones = cursor.fetchall()
            return {timezone[0] for timezone in timezones}

    def get_postgresql_default_table_access_methods(self) -> set[str]:
        """Returns the PostgreSQL available table access methods.

        Returns:
            Set of PostgreSQL table access methods.
        """
        with (
            self._connect_to_database(database_host=self.current_host) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT amname FROM pg_am WHERE amtype = 't';")
            access_methods = cursor.fetchall()
            return {access_method[0] for access_method in access_methods}

    def is_tls_enabled(self, check_current_host: bool = False) -> bool:
        """Returns whether TLS is enabled.

        Args:
            check_current_host: whether to check the current host
                instead of the primary host.

        Returns:
            whether TLS is enabled.
        """
        try:
            with (
                self._connect_to_database(
                    database_host=self.current_host if check_current_host else None
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SHOW ssl;")
                # SSL state should always be set
                return "on" in cursor.fetchone()[0]
        except psycopg2.Error:
            # Connection errors happen when PostgreSQL has not started yet.
            return False

    def list_access_groups(self, current_host=False) -> set[str]:
        """Returns the list of PostgreSQL database access groups.

        Args:
            current_host: whether to check the current host
                instead of the primary host.

        Returns:
            List of PostgreSQL database access groups.
        """
        connection = None
        host = self.current_host if current_host else None
        try:
            with (
                self._connect_to_database(database_host=host) as connection,
                connection.cursor() as cursor,
            ):
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

    def list_accessible_databases_for_user(self, user: str, current_host=False) -> set[str]:
        """Returns the list of accessible databases for a specific user.

        Args:
            user: the user to check.
            current_host: whether to check the current host
                instead of the primary host.

        Returns:
            List of accessible database (the ones where
                the user has the CONNECT privilege).
        """
        connection = None
        host = self.current_host if current_host else None
        try:
            with (
                self._connect_to_database(database_host=host) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    SQL(
                        "SELECT TRUE FROM pg_catalog.pg_user WHERE usename = {} AND usesuper;"
                    ).format(Literal(user))
                )
                if cursor.fetchone() is not None:
                    return {"all"}
                cursor.execute(
                    SQL(
                        "SELECT datname FROM pg_catalog.pg_database WHERE has_database_privilege({}, datname, 'CONNECT') AND NOT datistemplate;"
                    ).format(Literal(user))
                )
                databases = cursor.fetchall()
                return {database[0] for database in databases}
        except psycopg2.Error as e:
            logger.error(f"Failed to list accessible databases for user {user}: {e}")
            raise PostgreSQLListAccessibleDatabasesForUserError() from e
        finally:
            if connection is not None:
                connection.close()

    def list_users(self, group: str | None = None, current_host=False) -> set[str]:
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
            with (
                self._connect_to_database(database_host=host) as connection,
                connection.cursor() as cursor,
            ):
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

    def list_users_from_relation(self, current_host=False) -> set[str]:
        """Returns the list of PostgreSQL database users that were created by a relation.

        Args:
            current_host: whether to check the current host
                instead of the primary host.

        Returns:
            List of PostgreSQL database users.
        """
        connection = None
        host = self.current_host if current_host else None
        try:
            with (
                self._connect_to_database(database_host=host) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT usename "
                    "FROM pg_catalog.pg_user "
                    "WHERE usename LIKE 'relation_id_%' OR usename LIKE 'relation-%' "
                    "OR usename LIKE 'pgbouncer_auth_relation_%' OR usename LIKE '%_user_%_%' "
                    "OR usename LIKE 'logical_replication_relation_%';"
                )
                usernames = cursor.fetchall()
                return {username[0] for username in usernames}
        except psycopg2.Error as e:
            logger.error(f"Failed to list PostgreSQL database users: {e}")
            raise PostgreSQLListUsersError() from e
        finally:
            if connection is not None:
                connection.close()

    def list_existing_roles(self) -> set[str]:
        """Returns a set containing the existing roles.

        Returns:
            Set containing the existing roles.
        """
        with self._connect_to_database() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT rolname FROM pg_roles;")
            return {role[0] for role in cursor.fetchall() if role[0]}

    def list_valid_privileges_and_roles(self) -> tuple[set[str], set[str]]:
        """Returns two sets with valid privileges and roles.

        Returns:
            Tuple containing two sets: the first with valid privileges
                and the second with valid roles.
        """
        return {
            "superuser",
        }, ALLOWED_ROLES

    def _get_existing_databases(self) -> list[str]:
        # Template1 should go first
        databases = ["template1"]
        connection = None
        cursor = None
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT datname FROM pg_database WHERE datname <> 'template0' AND datname <> 'template1';"
                )
                db = cursor.fetchone()
                while db:
                    databases.append(db[0])
                    db = cursor.fetchone()
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
        return databases

    def _handle_temp_tablespace_on_reboot(
        self, cursor, temp_location: str, temp_tablespace_exists: bool
    ) -> None:
        """Handle temp tablespace when permissions need fixing after reboot.

        Args:
            cursor: Database cursor.
            temp_location: Path to the temp tablespace location.
            temp_tablespace_exists: Whether the temp tablespace already exists.
        """
        if not temp_tablespace_exists:
            return

        # Different handling based on storage type
        if is_tmpfs(temp_location):
            # tmpfs: Directory is empty after reboot, safe to rename and recreate
            # Rename existing temp tablespace instead of dropping it.
            # Timestamp collision is not possible: the charm ensures this code runs leader-only,
            # and it executes within a single database transaction holding exclusive locks.
            new_name = f"temp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
            cursor.execute(f"ALTER TABLESPACE temp RENAME TO {new_name};")

            # List temp tablespaces with suffix for operator follow-up cleanup
            cursor.execute("SELECT spcname FROM pg_tablespace WHERE spcname LIKE 'temp_%';")
            temp_tbls = sorted([row[0] for row in cursor.fetchall()])
            logger.info(
                "There are %d temp tablespaces that should be checked and removed: %s",
                len(temp_tbls),
                ", ".join(temp_tbls),
            )
        else:
            # Persistent storage: Tablespace is still valid, permissions already fixed
            # Log that we fixed permissions but didn't recreate
            logger.info(
                "Fixed permissions on temp tablespace directory at %s (persistent storage), "
                "existing tablespace remains valid",
                temp_location,
            )

    def set_up_database(self, temp_location: str | None = None) -> None:
        """Set up postgres database with the right permissions.

        This method configures the postgres database with appropriate permissions and
        optionally creates a temporary tablespace.

        Args:
            temp_location: Optional path for the temp tablespace. If provided, the method
                will ensure proper permissions and create the tablespace if it doesn't exist.

        Behavior on reboot:
            - For tmpfs storage: If permissions are incorrect after reboot, renames the old
              tablespace and creates a new one (tmpfs directory is empty after reboot).
            - For persistent storage: If permissions are incorrect after reboot, fixes
              permissions but keeps the existing tablespace (directory contents persist).

        Raises:
            PostgreSQLDatabasesSetupError: If database setup fails.
        """
        connection = None
        cursor = None
        try:
            connection = self._connect_to_database()
            cursor = connection.cursor()

            if temp_location is not None:
                # Fix permissions on the temporary tablespace location when a reboot happens.
                temp_location_stats = os.stat(temp_location)
                permissions_need_fix = self.substrate == Substrates.VM and (
                    pwd.getpwuid(temp_location_stats.st_uid).pw_name != SNAP_USER
                    or int(temp_location_stats.st_mode & 0o777) != POSTGRESQL_STORAGE_PERMISSIONS
                )

                if permissions_need_fix:
                    change_owner(temp_location)
                    os.chmod(temp_location, POSTGRESQL_STORAGE_PERMISSIONS)

                    # Check if temp tablespace exists and handle it appropriately
                    cursor.execute("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
                    temp_tablespace_exists = cursor.fetchone() is not None
                    self._handle_temp_tablespace_on_reboot(
                        cursor, temp_location, temp_tablespace_exists
                    )

                # Ensure a fresh temp tablespace exists at the expected location.
                cursor.execute("SELECT TRUE FROM pg_tablespace WHERE spcname='temp';")
                if cursor.fetchone() is None:
                    cursor.execute(f"CREATE TABLESPACE temp LOCATION '{temp_location}';")
                    cursor.execute("GRANT CREATE ON TABLESPACE temp TO public;")

            cursor.close()
            cursor = None
            connection.close()
            connection = None

            with (
                self._connect_to_database(database="template1") as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    f"SELECT TRUE FROM pg_roles WHERE rolname='{ROLE_DATABASES_OWNER}';"  # noqa: S608
                )
                if cursor.fetchone() is None:
                    self.create_user(
                        ROLE_DATABASES_OWNER,
                        can_create_database=True,
                        extra_user_roles=[ROLE_DML],
                    )

                self.set_up_login_hook_function()
                self.set_up_predefined_catalog_roles_function()

            connection.close()
            connection = None

            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute("REVOKE ALL PRIVILEGES ON DATABASE postgres FROM PUBLIC;")
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC;")
                for user in self.system_users:
                    cursor.execute(
                        SQL("GRANT ALL PRIVILEGES ON DATABASE postgres TO {};").format(
                            Identifier(user)
                        )
                    )
        except psycopg2.Error as e:
            logger.error(f"Failed to set up databases: {e}")
            raise PostgreSQLDatabasesSetupError() from e
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def set_up_login_hook_function(self) -> None:
        """Create a login hook function to set the user for the current session."""
        function_creation_statement = f"""CREATE OR REPLACE FUNCTION login_hook.login() RETURNS VOID AS $$
DECLARE
	ex_state TEXT;
	ex_message TEXT;
	ex_detail TEXT;
	ex_hint TEXT;
	ex_context TEXT;
	cur_user TEXT;
	db_admin_role TEXT;
	db_name TEXT;
	db_owner_role TEXT;
	is_user_admin BOOLEAN;
    user_has_createdb BOOLEAN;
BEGIN
	IF NOT login_hook.is_executing_login_hook()
	THEN
		RAISE EXCEPTION 'The login_hook.login() function should only be invoked by the login_hook code';
	END IF;

	cur_user := (SELECT current_user);

	EXECUTE 'SELECT current_database()' INTO db_name;
	db_admin_role = 'charmed_' || db_name || '_admin';

	EXECUTE format('SELECT EXISTS(SELECT * FROM pg_auth_members a, pg_roles b, pg_roles c WHERE a.roleid = b.oid AND a.member = c.oid AND (b.rolname = %L OR b.rolname = %L) and c.rolname = %L)', db_admin_role, '{ROLE_ADMIN}', cur_user) INTO is_user_admin;

    EXECUTE format('SELECT EXISTS(SELECT * FROM pg_auth_members a, pg_roles b, pg_roles c WHERE a.roleid = b.oid AND a.member = c.oid AND b.rolname = %L and c.rolname = %L)', '{ROLE_DATABASES_OWNER}', cur_user) INTO user_has_createdb;

	BEGIN
        IF is_user_admin = true THEN
			db_owner_role = 'charmed_' || db_name || '_owner';
			EXECUTE format('SET ROLE %L', db_owner_role);
		ELSE
            IF user_has_createdb = true THEN
                EXECUTE format('SET ROLE %L', '{ROLE_DATABASES_OWNER}');
            END IF;
		END IF;
	EXCEPTION
		WHEN OTHERS THEN
			GET STACKED DIAGNOSTICS ex_state = RETURNED_SQLSTATE, ex_message = MESSAGE_TEXT, ex_detail = PG_EXCEPTION_DETAIL, ex_hint = PG_EXCEPTION_HINT, ex_context = PG_EXCEPTION_CONTEXT;
			RAISE LOG e'Error in login_hook.login()\nsqlstate %\nmessage: %\ndetail: %\nhint: %\ncontext: %', ex_state, ex_message, ex_detail, ex_hint, ex_context;
	END;
END;
$$ LANGUAGE plpgsql;"""  # noqa: S608
        connection = None
        try:
            for database in self._get_existing_databases():
                with (
                    self._connect_to_database(database=database) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute(SQL("CREATE EXTENSION IF NOT EXISTS login_hook;"))
                    cursor.execute(SQL("CREATE SCHEMA IF NOT EXISTS login_hook;"))
                    cursor.execute(SQL(function_creation_statement))
                    cursor.execute(SQL("GRANT EXECUTE ON FUNCTION login_hook.login() TO PUBLIC;"))
        except psycopg2.Error as e:
            logger.error(f"Failed to create login hook function: {e}")
            raise e
        finally:
            if connection:
                connection.close()

    def set_up_predefined_catalog_roles_function(self) -> None:
        """Create predefined catalog roles function."""
        function_creation_statement = f"""CREATE OR REPLACE FUNCTION set_up_predefined_catalog_roles() RETURNS VOID AS $$
DECLARE
    database TEXT;
    current_session_user TEXT;
    owner_user TEXT;
    admin_user TEXT;
    dml_user TEXT;
    statements TEXT[];
    statement TEXT;
BEGIN
	database := (SELECT current_database());
	current_session_user := (SELECT session_user);
    owner_user := quote_ident('charmed_' || database || '_owner');
    admin_user := quote_ident('charmed_' || database || '_admin');
    dml_user := quote_ident('charmed_' || database || '_dml');

    IF (SELECT COUNT(rolname) FROM pg_roles WHERE rolname=FORMAT('%s', 'charmed_' || database || '_owner')) = 0 THEN
        statements := ARRAY[
            'CREATE ROLE ' || owner_user || ' NOSUPERUSER NOCREATEDB NOCREATEROLE NOLOGIN NOREPLICATION;',
            'CREATE ROLE ' || admin_user || ' NOSUPERUSER NOCREATEDB NOCREATEROLE NOLOGIN NOREPLICATION NOINHERIT IN ROLE ' || owner_user || ';',
            'CREATE ROLE ' || dml_user || ' NOSUPERUSER NOCREATEDB NOCREATEROLE NOLOGIN NOREPLICATION;',
            'GRANT ' || owner_user || ' TO {ROLE_ADMIN} WITH INHERIT FALSE;'
        ];
        FOREACH statement IN ARRAY statements
        LOOP
            EXECUTE statement;
        END LOOP;
    END IF;

    database := quote_ident(database);

    statements := ARRAY[
        'REVOKE CREATE ON DATABASE ' || database || ' FROM {ROLE_DATABASES_OWNER};',
        'ALTER SCHEMA public OWNER TO ' || owner_user || ';',
        'GRANT CONNECT ON DATABASE ' || database || ' TO ' || admin_user || ';',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_STATS};',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_READ};',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_DML};',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_BACKUP};',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_DBA};',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_ADMIN};',
        'GRANT ' || admin_user || ' TO {ROLE_ADMIN} WITH INHERIT FALSE;',
        'GRANT CONNECT ON DATABASE ' || database || ' TO {ROLE_DATABASES_OWNER};'
    ];
    FOREACH statement IN ARRAY statements
    LOOP
        EXECUTE statement;
    END LOOP;

    IF current_session_user LIKE 'relation-%' OR current_session_user LIKE 'relation_id_%' THEN
        RAISE NOTICE 'Granting % to %', admin_user, current_session_user;
        statements := ARRAY[
            'GRANT ' || admin_user || ' TO "' || current_session_user || '" WITH INHERIT FALSE;',
            'GRANT ' || dml_user || ' TO "' || current_session_user || '" WITH INHERIT TRUE;'
        ];
        FOREACH statement IN ARRAY statements
        LOOP
            EXECUTE statement;
        END LOOP;
    END IF;

    statements := ARRAY[
        'GRANT CREATE ON DATABASE ' || database || ' TO ' || owner_user || ';',
        'GRANT TEMPORARY ON DATABASE ' || database || ' TO ' || owner_user || ';',
        'ALTER DEFAULT PRIVILEGES FOR ROLE ' || owner_user || ' GRANT SELECT ON TABLES TO ' || admin_user || ';',
        'ALTER DEFAULT PRIVILEGES FOR ROLE ' || owner_user || ' GRANT EXECUTE ON FUNCTIONS TO ' || admin_user || ';',
        'ALTER DEFAULT PRIVILEGES FOR ROLE ' || owner_user || ' GRANT SELECT ON SEQUENCES TO ' || admin_user || ';',
        'GRANT EXECUTE ON FUNCTION set_user_u(text) TO charmed_dba;',
        'REVOKE EXECUTE ON FUNCTION set_user_u(text) FROM ' || owner_user || ';',
        'REVOKE EXECUTE ON FUNCTION set_user_u(text) FROM ' || admin_user || ';',
        'GRANT EXECUTE ON FUNCTION set_user(text) TO charmed_dba;',
        'REVOKE EXECUTE ON FUNCTION set_user(text) FROM ' || owner_user || ';',
        'REVOKE EXECUTE ON FUNCTION set_user(text) FROM ' || admin_user || ';',
        'GRANT EXECUTE ON FUNCTION set_user(text, text) TO charmed_dba;',
        'REVOKE EXECUTE ON FUNCTION set_user(text, text) FROM ' || owner_user || ';',
        'REVOKE EXECUTE ON FUNCTION set_user(text, text) FROM ' || admin_user || ';',
        'GRANT EXECUTE ON FUNCTION reset_user() TO charmed_dba;',
        'REVOKE EXECUTE ON FUNCTION reset_user() FROM ' || owner_user || ';',
        'REVOKE EXECUTE ON FUNCTION reset_user() FROM ' || admin_user || ';',
        'GRANT EXECUTE ON FUNCTION reset_user(text) TO charmed_dba;',
        'REVOKE EXECUTE ON FUNCTION reset_user(text) FROM ' || owner_user || ';',
        'REVOKE EXECUTE ON FUNCTION reset_user(text) FROM ' || admin_user || ';'
    ];
    FOREACH statement IN ARRAY statements
    LOOP
        EXECUTE statement;
    END LOOP;
END;
$$ LANGUAGE plpgsql security definer;"""  # noqa: S608
        connection = None
        try:
            for database in self._get_existing_databases():
                with (
                    self._connect_to_database(database=database) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute(SQL(function_creation_statement))
                    cursor.execute(
                        SQL("ALTER FUNCTION set_up_predefined_catalog_roles OWNER TO operator;")
                    )
                    cursor.execute(
                        SQL(
                            "REVOKE EXECUTE ON FUNCTION set_up_predefined_catalog_roles FROM PUBLIC;"
                        )
                    )
                    cursor.execute(
                        SQL(
                            "GRANT EXECUTE ON FUNCTION set_up_predefined_catalog_roles TO {};"
                        ).format(Identifier(ROLE_DATABASES_OWNER))
                    )
                    cursor.execute(
                        SQL("REVOKE CREATE ON DATABASE {} FROM {};").format(
                            Identifier("template1"), Identifier(ROLE_DATABASES_OWNER)
                        )
                    )
        except psycopg2.Error as e:
            logger.error(f"Failed to set up predefined catalog roles function: {e}")
            raise PostgreSQLCreatePredefinedRolesError() from e
        finally:
            if connection:
                connection.close()

    def update_user_password(
        self, username: str, password: str, database_host: str | None = None
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
            with (
                self._connect_to_database(database_host=database_host) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(SQL("RESET ROLE;"))
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

    def database_exists(self, db: str) -> bool:
        """Check whether specified database exists."""
        connection = None
        try:
            connection = self._connect_to_database()
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("SELECT datname FROM pg_database WHERE datname={};").format(Literal(db))
                )
                return cursor.fetchone() is not None
        except psycopg2.Error as e:
            logger.error(f"Failed to check Postgresql database existence: {e}")
            raise PostgreSQLDatabaseExistsError() from e
        finally:
            if connection:
                connection.close()

    def table_exists(self, db: str, schema: str, table: str) -> bool:
        """Check whether specified table in database exists."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "SELECT tablename FROM pg_tables WHERE schemaname={} AND tablename={};"
                    ).format(Literal(schema), Literal(table))
                )
                return cursor.fetchone() is not None
        except psycopg2.Error as e:
            logger.error(f"Failed to check Postgresql table existence: {e}")
            raise PostgreSQLTableExistsError() from e
        finally:
            if connection:
                connection.close()

    def is_table_empty(self, db: str, schema: str, table: str) -> bool:
        """Check whether table is empty."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(SQL("SELECT COUNT(1) FROM {};").format(Identifier(schema, table)))
                if result := cursor.fetchone():
                    return result[0] == 0
                return True
        except psycopg2.Error as e:
            logger.error(f"Failed to check whether table is empty: {e}")
            raise PostgreSQLIsTableEmptyError() from e
        finally:
            if connection:
                connection.close()

    def create_publication(self, db: str, name: str, schematables: list[str]) -> None:
        """Create PostgreSQL publication."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("CREATE PUBLICATION {} FOR TABLE {};").format(
                        Identifier(name),
                        SQL(",").join(
                            Identifier(schematable.split(".")[0], schematable.split(".")[1])
                            for schematable in schematables
                        ),
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to create Postgresql publication: {e}")
            raise PostgreSQLCreatePublicationError() from e
        finally:
            if connection:
                connection.close()

    def publication_exists(self, db: str, publication: str) -> bool:
        """Check whether specified subscription in database exists."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("SELECT pubname FROM pg_publication WHERE pubname={};").format(
                        Literal(publication)
                    )
                )
                return cursor.fetchone() is not None
        except psycopg2.Error as e:
            logger.error(f"Failed to check Postgresql publication existence: {e}")
            raise PostgreSQLPublicationExistsError() from e
        finally:
            if connection:
                connection.close()

    def alter_publication(self, db: str, name: str, schematables: list[str]) -> None:
        """Alter PostgreSQL publication."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("ALTER PUBLICATION {} SET TABLE {};").format(
                        Identifier(name),
                        SQL(",").join(
                            Identifier(schematable.split(".")[0], schematable.split(".")[1])
                            for schematable in schematables
                        ),
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to alter Postgresql publication: {e}")
            raise PostgreSQLAlterPublicationError() from e
        finally:
            if connection:
                connection.close()

    def drop_publication(self, db: str, publication: str) -> None:
        """Drop PostgreSQL publication."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("DROP PUBLICATION IF EXISTS {};").format(
                        Identifier(publication),
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to drop Postgresql publication: {e}")
            raise PostgreSQLDropPublicationError() from e
        finally:
            if connection:
                connection.close()

    def create_subscription(
        self,
        subscription: str,
        host: str,
        db: str,
        user: str,
        password: str,
        publication: str,
        replication_slot: str,
    ) -> None:
        """Create PostgreSQL subscription."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "CREATE SUBSCRIPTION {} CONNECTION {} PUBLICATION {} WITH (copy_data=true,create_slot=false,enabled=true,slot_name={});"
                    ).format(
                        Identifier(subscription),
                        Literal(f"host={host} dbname={db} user={user} password={password}"),
                        Identifier(publication),
                        Identifier(replication_slot),
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to create Postgresql subscription: {e}")
            raise PostgreSQLCreateSubscriptionError() from e
        finally:
            if connection:
                connection.close()

    def subscription_exists(self, db: str, subscription: str) -> bool:
        """Check whether specified subscription in database exists."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("SELECT subname FROM pg_subscription WHERE subname={};").format(
                        Literal(subscription)
                    )
                )
                return cursor.fetchone() is not None
        except psycopg2.Error as e:
            logger.error(f"Failed to check Postgresql subscription existence: {e}")
            raise PostgreSQLSubscriptionExistsError() from e
        finally:
            if connection:
                connection.close()

    def update_subscription(self, db: str, subscription: str, host: str, user: str, password: str):
        """Update PostgreSQL subscription connection details."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("ALTER SUBSCRIPTION {} CONNECTION {}").format(
                        Identifier(subscription),
                        Literal(f"host={host} dbname={db} user={user} password={password}"),
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to update Postgresql subscription: {e}")
            raise PostgreSQLUpdateSubscriptionError() from e
        finally:
            if connection:
                connection.close()

    def refresh_subscription(self, db: str, subscription: str):
        """Refresh PostgreSQL subscription to pull publication changes."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection.cursor() as cursor:
                cursor.execute(
                    SQL("ALTER SUBSCRIPTION {} REFRESH PUBLICATION").format(
                        Identifier(subscription)
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to refresh Postgresql subscription: {e}")
            raise PostgreSQLRefreshSubscriptionError() from e
        finally:
            if connection:
                connection.close()

    def drop_subscription(self, db: str, subscription: str) -> None:
        """Drop PostgreSQL subscription."""
        connection = None
        try:
            connection = self._connect_to_database(database=db)
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL("ALTER SUBSCRIPTION {} DISABLE;").format(
                        Identifier(subscription),
                    )
                )
                cursor.execute(
                    SQL("ALTER SUBSCRIPTION {} SET (slot_name=NONE);").format(
                        Identifier(subscription),
                    )
                )
                cursor.execute(
                    SQL("DROP SUBSCRIPTION {};").format(
                        Identifier(subscription),
                    )
                )
        except psycopg2.Error as e:
            logger.error(f"Failed to drop Postgresql subscription: {e}")
            raise PostgreSQLDropSubscriptionError() from e
        finally:
            if connection:
                connection.close()

    @staticmethod
    def build_postgresql_group_map(group_map: str | None) -> list[tuple]:
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

            if psql_group in ACCESS_GROUPS:
                logger.warning(f"Tried to assign LDAP users to forbidden group: {psql_group}")
                continue

            group_map_list.append((ldap_group, psql_group))

        return group_map_list

    @staticmethod
    def build_postgresql_parameters(
        config_options: ConfigData, available_memory: int, limit_memory: int | None = None
    ) -> dict | None:
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
            separator = "-" if "-" in config else "_"
            parameter = "_".join(config.split(separator)[1:])
            if parameter in ["date_style", "time_zone"]:
                parameter = "".join(x.capitalize() for x in parameter.split("_"))
            elif parameter.startswith("pg_stat_statements"):
                parameter = "pg_stat_statements." + parameter.removeprefix("pg_stat_statements_")
            elif parameter == "maximum_lag_on_failover":
                continue
            parameters[parameter] = value
        shared_buffers_max_value_in_mb = int(available_memory * 0.4 / 10**6)
        shared_buffers_max_value = int(shared_buffers_max_value_in_mb * 10**3 / 8)
        if int(parameters.get("shared_buffers", 0)) > shared_buffers_max_value:
            raise Exception(
                f"Shared buffers config option should be at most 40% of the available memory, which is {shared_buffers_max_value_in_mb}MB"
            )
        if profile == "production":
            if "shared_buffers" in parameters:
                # Convert to bytes to use in the calculation.
                shared_buffers = int(parameters["shared_buffers"]) * 8 * 10**3
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
            with (
                self._connect_to_database(database_host=self.current_host) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    SQL(
                        "SET DateStyle to {};",
                    ).format(Identifier(date_style))
                )
            return True
        except psycopg2.Error:
            return False

    def validate_group_map(self, group_map: str | None) -> bool:
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
            parsed_group_map = self.build_postgresql_group_map(group_map)
        except ValueError:
            return False

        for _, psql_group in parsed_group_map:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                query = SQL("SELECT TRUE FROM pg_roles WHERE rolname={};")
                query = query.format(Literal(psql_group))
                cursor.execute(query)

                if cursor.fetchone() is None:
                    return False

        return True

    def drop_hba_triggers(self) -> None:
        """Drop pg_hba triggers on schema change."""
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "SELECT datname FROM pg_database WHERE datname <> 'template0' AND datname <>'postgres';"
                    )
                )
                databases = [row[0] for row in cursor.fetchall()]
        except psycopg2.Error as e:
            logger.warning(f"Failed to get databases when removing hba trigger: {e}")
            return
        finally:
            if connection:
                connection.close()

            # Existing objects need to be reassigned in each database
            # before the user can be deleted.

        for database in databases:
            try:
                with (
                    self._connect_to_database(database) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute(
                        SQL("DROP EVENT TRIGGER IF EXISTS update_pg_hba_on_create_schema;")
                    )
                    cursor.execute(
                        SQL("DROP EVENT TRIGGER IF EXISTS update_pg_hba_on_drop_schema;")
                    )
            except psycopg2.Error as e:
                logger.warning(f"Failed to remove hba trigger for {database}: {e}")
            finally:
                if connection:
                    connection.close()

    def list_databases(self, prefix: str | None = None) -> list[str]:
        """List non-system databases starting with prefix."""
        prefix_stmt = (
            SQL(" AND datname LIKE {}").format(Literal(prefix + "%")) if prefix else SQL("")
        )
        try:
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(
                    SQL(
                        "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <>'postgres'{};"
                    ).format(prefix_stmt)
                )
                return [row[0] for row in cursor.fetchall()]
        except psycopg2.Error as e:
            raise PostgreSQLListDatabasesError() from e
        finally:
            if connection:
                connection.close()

    def add_user_to_databases(
        self, user: str, databases: list[str], extra_user_roles: list[str] | None = None
    ) -> None:
        """Grant user access to a database."""
        try:
            roles, _ = self._process_extra_user_roles(user, extra_user_roles)
            connect_stmt = []
            for database in databases:
                db_roles, db_connect_stmt = self._adjust_user_roles(user, roles, database)
                roles += db_roles
                connect_stmt += db_connect_stmt
            with self._connect_to_database() as connection, connection.cursor() as cursor:
                cursor.execute(SQL("RESET ROLE;"))
                cursor.execute(SQL("BEGIN;"))
                cursor.execute(SQL("SET LOCAL log_statement = 'none';"))
                cursor.execute(SQL("COMMIT;"))

                # Add extra user roles to the new user.
                for role in roles:
                    cursor.execute(
                        SQL("GRANT {} TO {};").format(Identifier(role), Identifier(user))
                    )
                for statement in connect_stmt:
                    cursor.execute(statement)
        except psycopg2.Error as e:
            logger.error(f"Failed to add user: {e}")
            raise PostgreSQLUpdateUserError() from e

    def remove_user_from_databases(self, user: str, databases: list[str]) -> None:
        """Revoke user access to a database."""
        try:
            for database in databases:
                with self._connect_to_database() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        SQL("REVOKE CONNECT ON DATABASE {} FROM {};").format(
                            Identifier(database), Identifier(user)
                        )
                    )
                    cursor.execute(
                        SQL("REVOKE {} FROM {};").format(
                            Identifier(f"charmed_{database}_owner"), Identifier(user)
                        )
                    )
                    cursor.execute(
                        SQL("REVOKE {} FROM {};").format(
                            Identifier(f"charmed_{database}_admin"), Identifier(user)
                        )
                    )
                    cursor.execute(
                        SQL("REVOKE {} FROM {};").format(
                            Identifier(f"charmed_{database}_dml"), Identifier(user)
                        )
                    )
        except psycopg2.Error as e:
            logger.error(f"Failed to remove user: {e}")
            raise PostgreSQLUpdateUserError() from e
