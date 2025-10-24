# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres client relation hooks & helpers."""

import json
import logging
import typing

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProvides,
    DatabaseRequestedEvent,
)
from ops import ActiveStatus, BlockedStatus, ModelError, Object, Relation, RelationBrokenEvent
from single_kernel_postgresql.config.literals import SYSTEM_USERS
from single_kernel_postgresql.utils.postgresql import (
    ACCESS_GROUP_RELATION,
    ACCESS_GROUPS,
    INVALID_DATABASE_NAME_BLOCKING_MESSAGE,
    INVALID_DATABASE_NAMES,
    INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
    PostgreSQLBaseError,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
)

from constants import APP_SCOPE, DATABASE_MAPPING_LABEL, DATABASE_PORT, USERNAME_MAPPING_LABEL
from utils import label2name, new_password

logger = logging.getLogger(__name__)


# Label not a secret
NO_ACCESS_TO_SECRET_MSG = "Missing grant to requested entity secret"  # noqa: S105
FORBIDDEN_USER_MSG = "Requesting an existing username"
PREFIX_TOO_SHORT_MSG = "Prefix too short"

if typing.TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


class PrefixDatabaseCacheType(typing.TypedDict):
    """Type definition for the prefix database cached mapping."""

    username: str
    prefix: str
    databases: list[str]


class PostgreSQLProvider(Object):
    """Defines functionality for the 'provides' side of the 'postgresql-client' relation.

    Hook events observed:
        - database-requested
        - relation-broken
    """

    def __init__(self, charm: "PostgresqlOperatorCharm", relation_name: str = "database") -> None:
        """Constructor for PostgreSQLClientProvides object.

        Args:
            charm: the charm for which this relation is provided
            relation_name: the name of the relation
        """
        self.relation_name = relation_name

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_broken, self._on_relation_broken
        )
        self.charm = charm

        # Charm events defined in the database provides charm library.
        self.database_provides = DatabaseProvides(self.charm, relation_name=self.relation_name)
        self.framework.observe(
            self.database_provides.on.database_requested, self._on_database_requested
        )

    @staticmethod
    def _sanitize_extra_roles(extra_roles: str | None) -> list[str]:
        """Standardize and sanitize user extra-roles."""
        if extra_roles is None:
            return []

        # Make sure the access-groups are not in the list
        extra_roles_list = [role.lower() for role in extra_roles.split(",")]
        extra_roles_list = [role for role in extra_roles_list if role not in ACCESS_GROUPS]
        return extra_roles_list

    def get_username_mapping(self) -> dict[str, str]:
        """Get a mapping of custom usernames by a relation ID."""
        if username_mapping := self.charm.get_secret(APP_SCOPE, USERNAME_MAPPING_LABEL):
            return json.loads(username_mapping)
        return {}

    def update_username_mapping(self, relation_id: int, username: str | None) -> None:
        """Update a mapping of custom usernames in the application peer secret."""
        if username == f"relation-{relation_id}":
            return

        username_mapping = self.get_username_mapping()
        if username and username_mapping.get(str(relation_id)) != username:
            username_mapping[str(relation_id)] = username
        elif not username and str(relation_id) in username_mapping:
            del username_mapping[str(relation_id)]
        else:
            # Cache is up to date
            return
        self.charm.set_secret(APP_SCOPE, USERNAME_MAPPING_LABEL, json.dumps(username_mapping))

    def get_databases_prefix_mapping(self) -> dict[str, PrefixDatabaseCacheType]:
        """Get a mapping of prefixed databases by relation ID."""
        if database_mapping := self.charm.get_secret(APP_SCOPE, DATABASE_MAPPING_LABEL):
            return json.loads(database_mapping)
        return {}

    def set_databases_prefix_mapping(
        self,
        relation_id: int,
        username: str | None,
        prefix: str | None,
        databases: list[str] | None,
    ) -> None:
        """Set the initial mapping of prefix databases."""
        database_mapping = self.get_databases_prefix_mapping()
        # Empty databases is valid
        if prefix and username and databases is not None:
            database_mapping[str(relation_id)] = {
                "prefix": prefix,
                "username": username,
                "databases": databases,
            }
        elif not prefix and str(relation_id) in database_mapping:
            del database_mapping[str(relation_id)]
        else:
            # Cache is up to date
            return
        self.charm.set_secret(APP_SCOPE, DATABASE_MAPPING_LABEL, json.dumps(database_mapping))

    def add_database_to_prefix_mapping(self, database: str) -> list[str]:
        """Add a new database to all fitting prefixes."""
        usernames = []
        dirty = False
        database_mapping = self.get_databases_prefix_mapping()
        for value in database_mapping.values():
            if database.startswith(value["prefix"]):
                if database not in value["databases"]:
                    value["databases"].append(database)
                    value["databases"].sort()
                    dirty = True
                usernames.append(value["username"])
        if dirty:
            self.charm.set_secret(APP_SCOPE, DATABASE_MAPPING_LABEL, json.dumps(database_mapping))
        return usernames

    def remove_database_from_prefix_mapping(self, database: str) -> list[str]:
        """Remove a database from all fitting prefixes."""
        usernames = []
        database_mapping = self.get_databases_prefix_mapping()
        for value in database_mapping.values():
            if database in value["databases"]:
                value["databases"].remove(database)
                usernames.append(value["username"])
        if usernames:
            self.charm.set_secret(APP_SCOPE, DATABASE_MAPPING_LABEL, json.dumps(database_mapping))
        return usernames

    def set_rel_to_db_mapping(self) -> None:
        """Set mapping between relation and database."""
        if self.charm.unit.is_leader():
            self.charm.app_peer_data["rel_databases"] = json.dumps({
                key: val["database"]
                for key, val in self.database_provides.fetch_relation_data(
                    None, ["database"]
                ).items()
                if val.get("database")
            })

    def get_rel_to_db_mapping(self) -> dict[str, str] | None:
        """Set mapping between relation and database."""
        if self.charm.unit.is_leader():
            return json.loads(self.charm.app_peer_data.get("rel_databases", "{}"))

    def _get_credentials(self, event: DatabaseRequestedEvent) -> tuple[str, str] | None:
        try:
            if requested_entities := event.requested_entity_secret_content:
                for key, val in requested_entities.items():
                    if not val:
                        val = new_password()
                    if key in SYSTEM_USERS or key in self.charm.postgresql.list_users():
                        self.charm.unit.status = BlockedStatus(FORBIDDEN_USER_MSG)
                        return
                    return key, val
        except ModelError:
            self.charm.unit.status = BlockedStatus(NO_ACCESS_TO_SECRET_MSG)
            return
        return f"relation-{event.relation.id}", new_password()

    def _are_units_in_sync(self) -> bool:
        for key in self.charm.all_peer_data:
            # We skip the leader so we don't have to wait on the defer
            if (
                key != self.charm.app
                and key != self.charm.unit
                and self.charm.all_peer_data[key].get("user_hash", "")
                != self.charm.generate_user_hash
            ):
                return False
        return True

    def _on_database_requested(self, event: DatabaseRequestedEvent) -> None:
        """Generate password and handle user and database creation for the related application."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if not self.charm.unit.is_leader():
            return

        if (
            not self.charm.is_cluster_initialised
            or not self.charm._patroni.member_started
            or not self.charm.primary_endpoint
        ):
            event.defer()
            logger.debug(
                "Deferring on_database_requested: cluster not initialized, Patroni not started or primary endpoint not available"
            )
            return

        if creds := self._get_credentials(event):
            user, password = creds
        else:
            return

        # Retrieve the database name and extra user roles using the charm library.
        database = event.database or ""
        if database and database[-1] == "*":
            if len(database) < 4:
                self.charm.unit.status = BlockedStatus(PREFIX_TOO_SHORT_MSG)
                return
            databases = sorted(self.charm.postgresql.list_databases(database[:-1]))
            self.set_databases_prefix_mapping(event.relation.id, user, database[:-1], databases)
        else:
            databases = [database]
            # Add to cached field to be able to generate hba rules
            self.add_database_to_prefix_mapping(database)

        self.update_username_mapping(event.relation.id, user)
        self.charm.update_config()
        if not self._are_units_in_sync():
            logger.debug("Not all units have synced configuration")
            event.defer()
            return

        # Make sure the relation access-group is added to the list
        extra_user_roles = self._sanitize_extra_roles(event.extra_user_roles)
        extra_user_roles.append(ACCESS_GROUP_RELATION)

        try:
            # Creates the user and the database for this specific relation.
            plugins = self.charm.get_plugins()

            if database[-1] != "*":
                self.charm.postgresql.create_database(database, plugins=plugins)

                self.charm.postgresql.create_user(
                    user, password, extra_user_roles=extra_user_roles, database=database
                )
                # Get the prefixed users again, to add db level grants
                prefixed_users = self.add_database_to_prefix_mapping(database)
                for prefixed_user in prefixed_users:
                    self.charm.postgresql.add_user_to_databases(
                        prefixed_user, databases, extra_user_roles
                    )
            else:
                self.charm.postgresql.create_user(
                    user, password, extra_user_roles=extra_user_roles
                )

                self.charm.postgresql.add_user_to_databases(user, databases, extra_user_roles)

            # Share the credentials with the application.
            self.database_provides.set_credentials(event.relation.id, user, password)

            # Set the database version.
            self.database_provides.set_version(
                event.relation.id, self.charm.postgresql.get_postgresql_version()
            )

            # Set the database name
            self.database_provides.set_database(event.relation.id, database)

            # Update the read/write and read-only endpoints.
            self.update_endpoints(event)

            self._update_unit_status(event.relation)

            self.charm.update_config()
        except PostgreSQLBaseError as e:
            self.charm.set_unit_status(
                BlockedStatus(
                    e.message
                    if (
                        issubclass(type(e), PostgreSQLCreateDatabaseError)
                        or issubclass(type(e), PostgreSQLCreateUserError)
                    )
                    and e.message is not None
                    else f"Failed to initialize relation {self.relation_name}"
                )
            )
            return

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Correctly update the status."""
        self.update_username_mapping(event.relation.id, None)
        self.set_databases_prefix_mapping(event.relation.id, None, None, None)
        if (
            (dbs := self.get_rel_to_db_mapping())
            and (database := dbs.get(str(event.relation.id)))
            and database[-1] != "*"
        ):
            self.remove_database_from_prefix_mapping(database)

        self._update_unit_status(event.relation)

    def oversee_users(self) -> None:
        """Remove users from database if their relations were broken."""
        if not self.charm.unit.is_leader():
            return

        delete_user = "suppress-oversee-users" not in self.charm.app_peer_data

        # Retrieve database users.
        try:
            database_users = {
                user for user in self.charm.postgresql.list_users() if user.startswith("relation-")
            }
        except PostgreSQLBaseError as e:
            logger.error("Early-exit, failed to oversee users: %r", e)
            return

        # Retrieve the users from the active relations.
        relation_users = {
            f"relation-{relation.id}" for relation in self.model.relations[self.relation_name]
        }

        # Delete that users that exist in the database but not in the active relations.
        for user in database_users - relation_users:
            if delete_user:
                try:
                    logger.info("Remove relation user: %s", user)
                    self.charm.set_secret(APP_SCOPE, user, None)
                    self.charm.set_secret(APP_SCOPE, f"{user}-database", None)
                    self.charm.postgresql.delete_user(user)
                except PostgreSQLDeleteUserError:
                    logger.error("Failed to delete user %s", user)
            else:
                logger.info("Stale relation user detected: %s", user)

    def update_endpoints(self, event: DatabaseRequestedEvent | None = None) -> None:  # noqa: C901
        """Set the read/write and read-only endpoints."""
        if not self.charm.unit.is_leader():
            return

        # Get the current relation or all the relations
        # if this is triggered by another type of event.
        relations_ids = [event.relation.id] if event else None
        rel_data = self.database_provides.fetch_relation_data(
            relations_ids, ["external-node-connectivity", "database"]
        )

        # skip if no relation data
        if not rel_data:
            return

        secret_data = (
            self.database_provides.fetch_my_relation_data(relations_ids, ["username", "password"])
            or {}
        )

        # Get cluster status
        online_members = self.charm._patroni.online_cluster_members()
        # Filter out-of-sync members
        online_members = [
            member for member in online_members if not member.get("tags", {}).get("nosync", False)
        ]

        # populate rw/ro endpoints
        primary_unit_ip, rw_endpoint, ro_hosts, ro_endpoints = "", "", "", ""
        for member in online_members:
            unit = self.model.get_unit(label2name(member["name"]))
            if member["role"] == "leader":
                primary_unit_ip = self.charm._get_unit_ip(unit, self.relation_name)
                rw_endpoint = f"{primary_unit_ip}:{DATABASE_PORT}"
            else:
                replica_ip = self.charm._get_unit_ip(unit, self.relation_name)
                if not replica_ip:
                    continue
                if ro_hosts:
                    ro_hosts = f"{ro_hosts},{replica_ip}"
                    ro_endpoints = f"{ro_endpoints},{replica_ip}:{DATABASE_PORT}"
                else:
                    ro_hosts = replica_ip
                    ro_endpoints = f"{replica_ip}:{DATABASE_PORT}"
        else:
            if not ro_hosts and primary_unit_ip:
                # If there are no replicas, fallback to primary
                ro_endpoints = rw_endpoint
                ro_hosts = primary_unit_ip

        tls = "True" if self.charm.is_tls_enabled else "False"
        ca = None
        if tls == "True":
            _, ca, _ = self.charm.tls.get_client_tls_files()
        if not ca:
            ca = ""

        prefix_database_mapping = self.get_databases_prefix_mapping()

        for relation_id in rel_data:
            database = rel_data[relation_id].get("database")
            databases = None
            prefix_def = prefix_database_mapping.get(str(relation_id))
            if prefix_def is not None:
                databases = prefix_def["databases"]
                self.database_provides.set_prefix_databases(relation_id, databases)
                database = databases[0] if len(databases) else database
            user = secret_data.get(relation_id, {}).get("username")
            password = secret_data.get(relation_id, {}).get("password")
            if not database or not password:
                continue

            # Set the read/write endpoint.
            self.database_provides.set_endpoints(relation_id, rw_endpoint)

            # Set the read-only endpoint.
            self.database_provides.set_read_only_endpoints(relation_id, ro_endpoints)

            self.database_provides.set_tls(relation_id, tls)
            self.database_provides.set_tls_ca(relation_id, ca)
            if databases is None or len(databases):
                # Set connection string URI.
                self.database_provides.set_uris(
                    relation_id,
                    f"postgresql://{user}:{password}@{rw_endpoint}/{database}",
                )
                # Make sure that the URI will be a secret
                if (
                    secret_fields := self.database_provides.fetch_relation_field(
                        relation_id, "requested-secrets"
                    )
                ) and "read-only-uris" in secret_fields:
                    self.database_provides.set_read_only_uris(
                        relation_id,
                        f"postgresql://{user}:{password}@{ro_hosts}:{DATABASE_PORT}/{database}",
                    )
            self.set_rel_to_db_mapping()

    def _unblock_custom_user_errors(self, relation: Relation) -> None:
        if self.check_for_invalid_extra_user_roles(relation.id):
            self.charm.unit.status = BlockedStatus(INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE)
            return
        existing_users = self.charm.postgresql.list_users()
        for relation in self.charm.model.relations.get(self.relation_name, []):
            try:
                # Relation is not established and custom user was requested
                if not self.database_provides.fetch_my_relation_field(
                    relation.id, "secret-user"
                ) and (
                    secret_uri := self.database_provides.fetch_relation_field(
                        relation.id, "requested-entity-secret"
                    )
                ):
                    content = self.framework.model.get_secret(id=secret_uri).get_content()
                    for key in content:
                        if key in SYSTEM_USERS or key in existing_users:
                            logger.warning(
                                f"Relation {relation.id} is still requesting a forbidden user"
                            )
                            self.charm.unit.status = BlockedStatus(FORBIDDEN_USER_MSG)
                            return
            except ModelError:
                logger.warning(f"Relation {relation.id} still cannot access the set secret")
                self.charm.unit.status = BlockedStatus(NO_ACCESS_TO_SECRET_MSG)
                return
        self.charm.set_unit_status(ActiveStatus())

    def _update_unit_status(self, relation: Relation) -> None:
        """Clean up Blocked status if it's due to extensions request."""
        if (
            (
                self.charm.is_blocked
                and (
                    self.charm.unit.status.message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
                    or self.charm.unit.status.message == INVALID_DATABASE_NAME_BLOCKING_MESSAGE
                )
            )
            and not self.check_for_invalid_extra_user_roles(relation.id)
            and not self.check_for_invalid_database_name(relation.id)
        ):
            self.charm.set_unit_status(ActiveStatus())
        if (
            self.charm.is_blocked
            and "Failed to initialize relation" in self.charm.unit.status.message
        ):
            self.charm.set_unit_status(ActiveStatus())
        if self.charm.is_blocked and self.charm.unit.status.message == PREFIX_TOO_SHORT_MSG:
            for relation in self.charm.model.relations.get(self.relation_name, []):
                # Relation is not established and custom user was requested
                if (
                    (
                        database := self.database_provides.fetch_relation_field(
                            relation.id, "database"
                        )
                    )
                    and database[-1] == "*"
                    and len(database) < 4
                ):
                    return
                self.charm.set_unit_status(ActiveStatus())
                return
        if self.charm.is_blocked and self.charm.unit.status.message in [
            INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
            NO_ACCESS_TO_SECRET_MSG,
            FORBIDDEN_USER_MSG,
        ]:
            self._unblock_custom_user_errors(self, relation)

    def check_for_invalid_extra_user_roles(self, relation_id: int) -> bool:
        """Checks if there are relations with invalid extra user roles.

        Args:
            relation_id: current relation to be skipped.
        """
        valid_privileges, valid_roles = self.charm.postgresql.list_valid_privileges_and_roles()
        for relation in self.charm.model.relations.get(self.relation_name, []):
            if relation.id == relation_id:
                continue
            for data in relation.data.values():
                extra_user_roles = data.get("extra-user-roles")
                extra_user_roles = self._sanitize_extra_roles(extra_user_roles)
                for extra_user_role in extra_user_roles:
                    if (
                        extra_user_role not in valid_privileges
                        and extra_user_role not in valid_roles
                        and extra_user_role != "createdb"
                    ):
                        return True
        return False

    def check_for_invalid_database_name(self, relation_id: int) -> bool:
        """Checks if there are relations with invalid database names.

        Args:
            relation_id: current relation to be skipped.
        """
        for relation in self.charm.model.relations.get(self.relation_name, []):
            if relation.id == relation_id:
                continue
            for data in relation.data.values():
                database = data.get("database")
                if database is not None and (
                    len(database) > 49 or database in INVALID_DATABASE_NAMES
                ):
                    return True
        return False
