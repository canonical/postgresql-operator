# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres client relation hooks & helpers."""

import logging
import typing
from datetime import datetime

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProvides,
    DatabaseRequestedEvent,
)
from charms.postgresql_k8s.v1.postgresql import (
    ACCESS_GROUP_RELATION,
    ACCESS_GROUPS,
    INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
    PostgreSQLListUsersError,
)
from ops.charm import RelationBrokenEvent
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, Relation
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from constants import (
    ALL_CLIENT_RELATIONS,
    APP_SCOPE,
    DATABASE_PORT,
)
from utils import label2name, new_password

logger = logging.getLogger(__name__)


if typing.TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


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

        # Retrieve the database name and extra user roles using the charm library.
        database = event.database or ""

        # Make sure the relation access-group is added to the list
        extra_user_roles = self._sanitize_extra_roles(event.extra_user_roles)
        extra_user_roles.append(ACCESS_GROUP_RELATION)

        try:
            # Creates the user and the database for this specific relation.
            user = f"relation-{event.relation.id}"
            plugins = self.charm.get_plugins()

            self.charm.postgresql.create_database(database, plugins=plugins)

            password = new_password()
            self.charm.postgresql.create_user(
                user, password, extra_user_roles=extra_user_roles, in_role=f"{database}_admin"
            )

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
        except (
            PostgreSQLCreateDatabaseError,
            PostgreSQLCreateUserError,
            PostgreSQLGetPostgreSQLVersionError,
        ) as e:
            logger.exception(e)
            self.charm.set_unit_status(
                BlockedStatus(
                    e.message
                    if issubclass(type(e), PostgreSQLCreateUserError) and e.message is not None
                    else f"Failed to initialize relation {self.relation_name}"
                )
            )
            return

        # Try to wait for pg_hba trigger
        try:
            for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(1)):
                with attempt:
                    if not self.charm.postgresql.is_user_in_hba(user):
                        raise Exception("pg_hba not ready")
            self.charm.unit_peer_data.update({
                "pg_hba_needs_update_timestamp": str(datetime.now())
            })
        except RetryError:
            logger.warning("database requested: Unable to check pg_hba rule update")

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Correctly update the status."""
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
        except PostgreSQLListUsersError:
            return

        # Retrieve the users from the active relations.
        relations = [
            relation
            for relation_name, relations_list in self.model.relations.items()
            for relation in relations_list
            if relation_name in ALL_CLIENT_RELATIONS
        ]
        relation_users = set()
        for relation in relations:
            username = f"relation-{relation.id}"
            relation_users.add(username)

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

    def update_endpoints(self, event: DatabaseRequestedEvent = None) -> None:  # noqa: C901
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

        secret_data = self.database_provides.fetch_my_relation_data(relations_ids, ["password"])

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
        if tls == "True":
            _, ca, _ = self.charm.tls.get_client_tls_files()
        else:
            ca = ""

        for relation_id in rel_data:
            user = f"relation-{relation_id}"
            database = rel_data[relation_id].get("database")
            password = secret_data.get(relation_id, {}).get("password")
            if not database or not password:
                continue

            # Set the read/write endpoint.
            self.database_provides.set_endpoints(
                relation_id,
                rw_endpoint,
            )

            # Set the read-only endpoint.
            self.database_provides.set_read_only_endpoints(
                relation_id,
                ro_endpoints,
            )

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

            self.database_provides.set_tls(relation_id, tls)
            self.database_provides.set_tls_ca(relation_id, ca)

    def _update_unit_status(self, relation: Relation) -> None:
        """# Clean up Blocked status if it's due to extensions request."""
        if (
            self.charm.is_blocked
            and self.charm.unit.status.message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
        ) and not self.check_for_invalid_extra_user_roles(relation.id):
            self.charm.set_unit_status(ActiveStatus())
        if (
            self.charm.is_blocked
            and "Failed to initialize relation" in self.charm.unit.status.message
        ):
            self.charm.set_unit_status(ActiveStatus())

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
                    ):
                        return True
        return False
