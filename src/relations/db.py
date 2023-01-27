# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library containing the implementation of the legacy db and db-admin relations."""


import logging
from typing import Iterable

from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
)
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, Relation, Unit
from pgconnstr import ConnectionString

from constants import DATABASE_PORT
from utils import new_password

logger = logging.getLogger(__name__)

EXTENSIONS_BLOCKING_MESSAGE = "extensions requested through relation"


class DbProvides(Object):
    """Defines functionality for the 'provides' side of the 'db' relation.

    Hook events observed:
        - relation-changed
        - relation-departed
        - relation-broken
    """

    def __init__(self, charm: CharmBase, admin: bool = False):
        """Constructor for DbProvides object.

        Args:
            charm: the charm for which this relation is provided
            admin: a boolean defining whether this relation has admin permissions, switching
                between "db" and "db-admin" relations.
        """
        if admin:
            self.relation_name = "db-admin"
        else:
            self.relation_name = "db"

        super().__init__(charm, self.relation_name)

        self.framework.observe(
            charm.on[self.relation_name].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_departed, self._on_relation_departed
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken, self._on_relation_broken
        )

        self.admin = admin
        self.charm = charm

    def _check_for_blocking_relations(self, relation_id: int) -> bool:
        """Checks if there are relations with extensions.

        Args:
            relation_id: current relation to be skipped
        """
        for relname in ["db", "db-admin"]:
            for relation in self.charm.model.relations.get(relname, []):
                if relation.id == relation_id:
                    continue
                for data in relation.data.values():
                    if "extensions" in data:
                        return True
        return False

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the legacy db/db-admin relation changed event.

        Generate password and handle user and database creation for the related application.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if not self.charm.unit.is_leader():
            return

        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
            or not self.charm.primary_endpoint
        ):
            logger.debug(
                "Deferring on_relation_changed: cluster not initialized, Patroni not started or primary endpoint not available"
            )
            event.defer()
            return

        logger.warning(f"DEPRECATION WARNING - `{self.relation_name}` is a legacy interface")

        unit_relation_databag = event.relation.data[self.charm.unit]
        application_relation_databag = event.relation.data[self.charm.app]

        # Do not allow apps requesting extensions to be installed.
        if "extensions" in event.relation.data.get(
            event.app, {}
        ) or "extensions" in event.relation.data.get(event.unit, {}):
            logger.error(
                "ERROR - `extensions` cannot be requested through relations"
                " - they should be installed through a database charm config in the future"
            )
            self.charm.unit.status = BlockedStatus(EXTENSIONS_BLOCKING_MESSAGE)
            return

        # Sometimes a relation changed event is triggered,
        # and it doesn't have a database name in it.
        database = event.relation.data.get(event.app, {}).get(
            "database", event.relation.data.get(event.unit, {}).get("database")
        )
        if not database:
            logger.warning("No database name provided")
            event.defer()
            return

        try:
            # Creates the user and the database for this specific relation if it was not already
            # created in a previous relation changed event.
            user = f"relation-{event.relation.id}"
            password = unit_relation_databag.get("password", new_password())
            self.charm.set_secret("app", user, password)
            self.charm.set_secret("app", f"{user}-database", database)
            self.charm.postgresql.create_user(user, password, self.admin)
            self.charm.postgresql.create_database(database, user)
            postgresql_version = self.charm.postgresql.get_postgresql_version()
        except (
            PostgreSQLCreateDatabaseError,
            PostgreSQLCreateUserError,
            PostgreSQLGetPostgreSQLVersionError,
        ) as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"Failed to initialize {self.relation_name} relation"
            )
            return

        # Set the data in both application and unit data bag.
        # It's needed to run this logic on every relation changed event
        # setting the data again in the databag, otherwise the application charm that
        # is connecting to this database will receive a "database gone" event from the
        # old PostgreSQL library (ops-lib-pgsql) and the connection between the
        # application and this charm will not work.
        allowed_subnets = self._get_allowed_subnets(event.relation)
        allowed_units = self._get_allowed_units(event.relation)
        for databag in [application_relation_databag, unit_relation_databag]:
            updates = {
                "allowed-subnets": allowed_subnets,
                "allowed-units": allowed_units,
                "port": DATABASE_PORT,
                "version": postgresql_version,
                "user": user,
                "password": password,
                "database": database,
            }
            databag.update(updates)
        self.update_endpoints(event)

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the departure of legacy db and db-admin relations.

        Remove unit name from allowed_units key.
        """
        # Set a flag to avoid deleting database users when this unit
        # is removed and receives relation broken events from related applications.
        # This is needed because of https://bugs.launchpad.net/juju/+bug/1979811.
        # Neither peer relation data nor stored state are good solutions,
        # just a temporary solution.
        if event.departing_unit == self.charm.unit:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})
            # Just run the rest of the logic for departing of remote units.
            logger.debug("Early exit on_relation_departed: Skipping departing unit")
            return

        # Check for some conditions before trying to access the PostgreSQL instance.
        if not self.charm.unit.is_leader():
            return

        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
            or not self.charm.primary_endpoint
        ):
            logger.debug(
                "Deferring on_relation_departed: cluster not initialized, Patroni not started or primary endpoint not available"
            )
            event.defer()
            return

        departing_unit = event.departing_unit.name
        local_unit_data = event.relation.data[self.charm.unit]
        local_app_data = event.relation.data[self.charm.app]

        current_allowed_units = local_unit_data.get("allowed_units", "")

        logger.debug(f"Removing unit {departing_unit} from allowed_units")
        local_app_data["allowed_units"] = local_unit_data["allowed_units"] = " ".join(
            {unit for unit in current_allowed_units.split() if unit != departing_unit}
        )

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Remove the user created for this relation."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            not self.charm.unit.is_leader()
            or "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
            or not self.charm.primary_endpoint
        ):
            logger.debug(
                "Early exit on_relation_broken: Not leader, cluster not initialized, Patroni not started or no primary endpoint"
            )
            return

        # Run this event only if this unit isn't being
        # removed while the others from this application
        # are still alive. This check is needed because of
        # https://bugs.launchpad.net/juju/+bug/1979811.
        # Neither peer relation data nor stored state
        # are good solutions, just a temporary solution.
        if "departing" in self.charm._peers.data[self.charm.unit]:
            logger.debug("Early exit on_relation_broken: Skipping departing unit")
            return

        # Delete the user.
        user = f"relation-{event.relation.id}"
        try:
            self.charm.set_secret("app", user)
            self.charm.set_secret("app", f"{user}-database")
            self.charm.postgresql.delete_user(user)
        except PostgreSQLDeleteUserError as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"Failed to delete user during {self.relation_name} relation broken event"
            )

        # Clean up Blocked status if caused by the departed relation
        if (
            self.charm._has_blocked_status
            and self.charm.unit.status.message == EXTENSIONS_BLOCKING_MESSAGE
        ):
            if not self._check_for_blocking_relations(event.relation.id):
                self.charm.unit.status = ActiveStatus()

    def update_endpoints(self, event: RelationChangedEvent = None) -> None:
        """Set the read/write and read-only endpoints."""
        # Get the current relation or all the relations
        # if this is triggered by another type of event.
        relations = [event.relation] if event else self.model.relations[self.relation_name]

        primary_unit = self.charm._patroni.get_primary(unit_name_pattern=True)
        is_replica = self.charm.unit.name != primary_unit

        # List the replicas endpoints.
        replicas_endpoint = self.charm.members_ips - {self.charm.primary_endpoint}

        for relation in relations:
            # Retrieve some data from the relation.
            unit_relation_databag = relation.data[self.charm.unit]
            application_relation_databag = relation.data[self.charm.app]
            user = f"relation-{relation.id}"
            password = self.charm.get_secret("app", user)
            database = self.charm.get_secret("app", f"{user}-database")

            # If the relation data is not complete, the relations was not initialised yet.
            if not database or not user or not password:
                continue

            # Build the primary's connection string.
            primary_endpoint = str(
                ConnectionString(
                    host=self.charm.primary_endpoint,
                    dbname=database,
                    port=DATABASE_PORT,
                    user=user,
                    password=password,
                    fallback_application_name=relation.app.name,
                )
            )

            # If there are no replicas, remove the read-only endpoint.
            read_only_endpoints = (
                ",".join(
                    str(
                        ConnectionString(
                            host=replica_endpoint,
                            dbname=database,
                            port=DATABASE_PORT,
                            user=user,
                            password=password,
                            fallback_application_name=relation.app.name,
                        )
                    )
                    for replica_endpoint in replicas_endpoint
                )
                if len(replicas_endpoint) > 0
                else ""
            )

            # Set the read/write endpoint.
            data = {
                "host": self.charm.primary_endpoint,
                "master": primary_endpoint,
                "standbys": read_only_endpoints,
                "state": self._get_state(),
            }

            if is_replica:
                unit_relation_databag.clear()
                self.charm._peers.data[self.charm.unit].update({"replica": "True"})
            else:
                unit_relation_databag.update(data)
                self.charm._peers.data[self.charm.unit].update({"replica": ""})

            if self.charm.unit.is_leader():
                application_relation_databag.update(data)

    def _get_allowed_subnets(self, relation: Relation) -> str:
        """Build the list of allowed subnets as in the legacy charm."""

        def _comma_split(s) -> Iterable[str]:
            if s:
                for b in s.split(","):
                    b = b.strip()
                    if b:
                        yield b

        subnets = set()
        for unit, relation_data in relation.data.items():
            if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name):
                # Egress-subnets is not always available.
                subnets.update(set(_comma_split(relation_data.get("egress-subnets", ""))))
        return ",".join(sorted(subnets))

    def _get_allowed_units(self, relation: Relation) -> str:
        """Build the list of allowed units as in the legacy charm."""
        return ",".join(
            sorted(
                unit.name
                for unit in relation.data
                if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name)
            )
        )

    def _get_state(self) -> str:
        """Gets the given state for this unit.

        Returns:
            The state of this unit. Can be 'standalone', 'master', or 'standby'.
        """
        if len(self.charm._peers.units) == 0:
            return "standalone"
        if self.charm._patroni.get_primary(unit_name_pattern=True) == self.charm.unit.name:
            return "master"
        else:
            return "standby"
