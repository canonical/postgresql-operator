# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres client relation hooks & helpers."""

import logging

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProvides,
    DatabaseRequestedEvent,
)
from charms.postgresql_k8s.v0.postgresql import (
    INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
    PostgreSQLListUsersError,
)
from ops.charm import CharmBase, RelationBrokenEvent, RelationChangedEvent
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, Relation

from constants import (
    ALL_CLIENT_RELATIONS,
    APP_SCOPE,
    DATABASE_PORT,
    ENDPOINT_SIMULTANEOUSLY_BLOCKING_MESSAGE,
)
from utils import new_password

logger = logging.getLogger(__name__)


class PostgreSQLProvider(Object):
    """Defines functionality for the 'provides' side of the 'postgresql-client' relation.

    Hook events observed:
        - database-requested
        - relation-broken
    """

    def __init__(self, charm: CharmBase, relation_name: str = "database") -> None:
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
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed_event,
        )
        self.charm = charm

        # Charm events defined in the database provides charm library.
        self.database_provides = DatabaseProvides(self.charm, relation_name=self.relation_name)
        self.framework.observe(
            self.database_provides.on.database_requested, self._on_database_requested
        )

    def _on_database_requested(self, event: DatabaseRequestedEvent) -> None:
        """Generate password and handle user and database creation for the related application."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if not self.charm.unit.is_leader():
            return

        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
            or not self.charm.primary_endpoint
        ):
            event.defer()
            logger.debug(
                "Deferring on_database_requested: cluster not initialized, Patroni not started or primary endpoint not available"
            )
            return

        # Retrieve the database name and extra user roles using the charm library.
        database = event.database
        extra_user_roles = event.extra_user_roles

        try:
            # Creates the user and the database for this specific relation.
            user = f"relation-{event.relation.id}"
            password = new_password()
            self.charm.postgresql.create_user(user, password, extra_user_roles=extra_user_roles)
            plugins = self.charm.get_plugins()

            self.charm.postgresql.create_database(
                database, user, plugins=plugins, client_relations=self.charm.client_relations
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
        except (
            PostgreSQLCreateDatabaseError,
            PostgreSQLCreateUserError,
            PostgreSQLGetPostgreSQLVersionError,
        ) as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                e.message
                if issubclass(type(e), PostgreSQLCreateUserError) and e.message is not None
                else f"Failed to initialize {self.relation_name} relation"
            )

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

    def update_endpoints(self, event: DatabaseRequestedEvent = None) -> None:
        """Set the read/write and read-only endpoints."""
        if not self.charm.unit.is_leader():
            return

        # Get the current relation or all the relations
        # if this is triggered by another type of event.
        relations_ids = [event.relation.id] if event else None
        rel_data = self.database_provides.fetch_relation_data(
            relations_ids, ["external-node-connectivity", "database"]
        )
        secret_data = self.database_provides.fetch_my_relation_data(relations_ids, ["password"])

        # If there are no replicas, remove the read-only endpoint.
        replicas_endpoint = list(self.charm.members_ips - {self.charm.primary_endpoint})
        replicas_endpoint.sort()
        read_only_endpoints = (
            ",".join(f"{x}:{DATABASE_PORT}" for x in replicas_endpoint)
            if len(replicas_endpoint) > 0
            else ""
        )

        for relation_id in rel_data.keys():
            user = f"relation-{relation_id}"
            database = rel_data[relation_id].get("database")
            password = secret_data.get(relation_id, {}).get("password")

            # Set the read/write endpoint.
            self.database_provides.set_endpoints(
                relation_id,
                f"{self.charm.primary_endpoint}:{DATABASE_PORT}",
            )

            # Set the read-only endpoint.
            self.database_provides.set_read_only_endpoints(
                relation_id,
                read_only_endpoints,
            )

            # Set connection string URI.
            self.database_provides.set_uris(
                relation_id,
                f"postgresql://{user}:{password}@{self.charm.primary_endpoint}:{DATABASE_PORT}/{database}",
            )

    def _check_multiple_endpoints(self) -> bool:
        """Checks if there are relations with other endpoints."""
        relation_names = {relation.name for relation in self.charm.client_relations}
        if "database" in relation_names and len(relation_names) > 1:
            return True
        return False

    def _update_unit_status(self, relation: Relation) -> None:
        """# Clean up Blocked status if it's due to extensions request."""
        if (
            self.charm.is_blocked
            and self.charm.unit.status.message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
        ):
            if not self.check_for_invalid_extra_user_roles(relation.id):
                self.charm.unit.status = ActiveStatus()

        self._update_unit_status_on_blocking_endpoint_simultaneously()

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation has changed."""
        # Leader only
        if not self.charm.unit.is_leader():
            return

        if self._check_multiple_endpoints():
            self.charm.unit.status = BlockedStatus(ENDPOINT_SIMULTANEOUSLY_BLOCKING_MESSAGE)
            return

    def _update_unit_status_on_blocking_endpoint_simultaneously(self):
        """Clean up Blocked status if this is due related of multiple endpoints."""
        if (
            self.charm.is_blocked
            and self.charm.unit.status.message == ENDPOINT_SIMULTANEOUSLY_BLOCKING_MESSAGE
        ):
            if not self._check_multiple_endpoints():
                self.charm.unit.status = ActiveStatus()

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
                if extra_user_roles is None:
                    continue
                extra_user_roles = extra_user_roles.lower().split(",")
                for extra_user_role in extra_user_roles:
                    if (
                        extra_user_role not in valid_privileges
                        and extra_user_role not in valid_roles
                    ):
                        return True
        return False
