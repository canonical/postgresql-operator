# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library containing the implementation of the legacy db and db-admin relations."""


import logging
from typing import Iterable, List, Set, Tuple

from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
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

from constants import APP_SCOPE, DATABASE_PORT
from utils import new_password

logger = logging.getLogger(__name__)

EXTENSIONS_BLOCKING_MESSAGE = (
    "extensions requested through relation, enable them through config options"
)


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

        self.set_up_relation(event.relation)

    def _get_extensions(self, relation: Relation) -> Tuple[List, Set]:
        """Returns the list of required and disabled extensions."""
        requested_extensions = relation.data.get(relation.app, {}).get("extensions", "").split(",")
        for unit in relation.units:
            requested_extensions.extend(
                relation.data.get(unit, {}).get("extensions", "").split(",")
            )
        required_extensions = []
        for extension in requested_extensions:
            if extension != "" and extension not in required_extensions:
                required_extensions.append(extension)
        disabled_extensions = set()
        if required_extensions:
            for extension in required_extensions:
                extension_name = extension.split(":")[0]
                if not self.charm.model.config.get(f"plugin_{extension_name}_enable"):
                    disabled_extensions.add(extension_name)
        return required_extensions, disabled_extensions

    def set_up_relation(self, relation: Relation) -> bool:
        """Set up the relation to be used by the application charm."""
        # Do not allow apps requesting extensions to be installed
        # (let them now about config options).
        required_extensions, disabled_extensions = self._get_extensions(relation)
        if disabled_extensions:
            logger.error(
                f"ERROR - `extensions` ({', '.join(disabled_extensions)}) cannot be requested through relations"
                " - Please enable extensions through `juju config` and add the relation again."
            )
            self.charm.unit.status = BlockedStatus(EXTENSIONS_BLOCKING_MESSAGE)
            return False

        database = relation.data.get(relation.app, {}).get("database")
        if not database:
            for unit in relation.units:
                unit_database = relation.data.get(unit, {}).get("database")
                if unit_database:
                    database = unit_database
                    break

        # Sometimes a relation changed event is triggered, and it doesn't have
        # a database name in it (like the relation with Landscape server charm),
        # so create a database with the other application name.
        if not database:
            database = relation.app.name

        try:
            unit_relation_databag = relation.data[self.charm.unit]

            # Creates the user and the database for this specific relation if it was not already
            # created in a previous relation changed event.
            user = f"relation-{relation.id}"
            password = unit_relation_databag.get("password", new_password())

            # Store the user, password and database name in the secret store to be accessible by
            # non-leader units when the cluster topology changes.
            self.charm.set_secret(APP_SCOPE, user, password)
            self.charm.set_secret(APP_SCOPE, f"{user}-database", database)

            self.charm.postgresql.create_user(user, password, self.admin)
            self.charm.postgresql.create_database(database, user)

            # Enable/disable extensions in the new database.
            self.charm.enable_disable_extensions(database)
        except (PostgreSQLCreateDatabaseError, PostgreSQLCreateUserError) as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"Failed to initialize {self.relation_name} relation"
            )
            return False

        self.update_endpoints(relation)

        self._update_unit_status(relation)

        return True

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

        self._update_unit_status(event.relation)

    def _update_unit_status(self, relation: Relation) -> None:
        """Clean up Blocked status if it's due to extensions request."""
        if self.charm.is_blocked and self.charm.unit.status.message == EXTENSIONS_BLOCKING_MESSAGE:
            if not self._check_for_blocking_relations(relation.id):
                self.charm.unit.status = ActiveStatus()

    def update_endpoints(self, relation: Relation = None) -> None:
        """Set the read/write and read-only endpoints."""
        # Get the current relation or all the relations
        # if this is triggered by another type of event.
        relations = [relation] if relation else self.model.relations[self.relation_name]
        if len(relations) == 0:
            return

        try:
            postgresql_version = self.charm.postgresql.get_postgresql_version()
        except PostgreSQLGetPostgreSQLVersionError as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"Failed to retrieve the PostgreSQL version to initialise/update {self.relation_name} relation"
            )
            return

        # List the replicas endpoints.
        replicas_endpoint = self.charm.members_ips - {self.charm.primary_endpoint}

        for relation in relations:
            # Retrieve some data from the relation.
            unit_relation_databag = relation.data[self.charm.unit]
            user = f"relation-{relation.id}"
            password = self.charm.get_secret(APP_SCOPE, user)
            database = self.charm.get_secret(APP_SCOPE, f"{user}-database")

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
                        )
                    )
                    for replica_endpoint in replicas_endpoint
                )
                if len(replicas_endpoint) > 0
                else ""
            )

            # Set the read/write endpoint.
            allowed_subnets = self._get_allowed_subnets(relation)
            allowed_units = self._get_allowed_units(relation)
            data = {
                "allowed-subnets": allowed_subnets,
                "allowed-units": allowed_units,
                "host": self.charm.primary_endpoint,
                "port": DATABASE_PORT,
                "user": user,
                "schema_user": user,
                "version": postgresql_version,
                "password": password,
                "schema_password": password,
                "database": database,
                "master": primary_endpoint,
                "standbys": read_only_endpoints,
                "state": self._get_state(),
            }

            # Set the data only in the unit databag.
            unit_relation_databag.update(data)

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
        return " ".join(
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
            return "hot standby"
