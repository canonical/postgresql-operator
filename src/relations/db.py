# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library containing the implementation of the legacy db and db-admin relations."""


import logging
from typing import Iterable

from charms.postgresql.v0.postgresql_helpers import (
    connect_to_database,
    create_database,
    create_user,
    drop_user,
)
from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
)
from ops.framework import Object
from ops.model import BlockedStatus, Relation, Unit
from pgconnstr import ConnectionString

from constants import LEGACY_DB, LEGACY_DB_ADMIN
from utils import new_password

logger = logging.getLogger(__name__)


class LegacyRelation(Object):
    """Legacy `db` and ``db-admin relations implementation."""

    def __init__(self, charm: CharmBase):
        super().__init__(charm, "db-handler")

        self._charm = charm

        self.framework.observe(
            self._charm.on[LEGACY_DB].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            self._charm.on[LEGACY_DB].relation_departed, self._on_relation_departed
        )
        self.framework.observe(self._charm.on[LEGACY_DB].relation_broken, self._on_relation_broken)

        self.framework.observe(
            self._charm.on[LEGACY_DB_ADMIN].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            self._charm.on[LEGACY_DB_ADMIN].relation_departed, self._on_relation_departed
        )
        self.framework.observe(
            self._charm.on[LEGACY_DB_ADMIN].relation_broken, self._on_relation_broken
        )

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the legacy shared_db relation changed event.

        Generate password and handle user and database creation for the related application.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if not self._charm.unit.is_leader():
            return

        # Get the relation name to handle specific logic for each relation (db and db-admin).
        relation_name = event.relation.name

        logger.warning(f"DEPRECATION WARNING - `{relation_name}` is a legacy interface")

        unit_relation_databag = event.relation.data[self._charm.unit]
        application_relation_databag = event.relation.data[self._charm.app]

        if "extensions" in unit_relation_databag or "extensions" in application_relation_databag:
            logger.error(
                "ERROR - `extensions` cannot be requested through relations"
                " - they should be installed through a database charm config in the future"
            )
            self._charm.unit.status = BlockedStatus("extensions requested through relation")
            return

        # Connect to the PostgreSQL instance to later create a user and the database.
        hostname = self._charm._unit_ip
        connection = connect_to_database(
            "postgres", "postgres", hostname, self._charm._get_postgres_password()
        )

        user = f"relation_id_{event.relation.id}"
        password = unit_relation_databag.get("password", new_password())
        database = event.relation.data[event.app].get(
            "database", event.relation.data[event.unit].get("database")
        )
        # Sometimes a relation changed event is triggered,
        # and it doesn't have a database name in it.
        if not database:
            logger.warning("No database name provided")
            event.defer()
            return

        # Creates the user and the database for this specific relation if it was not already
        # created in a previous relation changed event.
        # Use the relation name to request or not a superuser (admin flag).
        create_user(connection, user, password, admin=relation_name == LEGACY_DB_ADMIN)
        create_database(connection, database, user)

        connection.close()

        # Get the list of all members in the cluster.
        # members = self._charm._patroni.cluster_members
        # Build the primary's connection string.
        primary = str(
            ConnectionString(
                host=self._charm._unit_ip,
                dbname=database,
                port=5432,
                user=user,
                password=password,
                fallback_application_name=event.app.name,
            )
        )
        # Build the standbys' connection strings.
        # standbys = ",".join(
        #     [
        standbys = str(
            ConnectionString(
                host=self._charm._unit_ip,
                dbname=database,
                port=5432,
                user=user,
                password=password,
                fallback_application_name=event.app.name,
            )
        )
        #         for member in members
        #         if self._charm._get_hostname_from_unit(member) != primary
        #     ]
        # )

        # Set the data in both application and unit data bag.
        # It 's needed to run this logic on every relation changed event
        # setting the data again in the databag, otherwise the application charm that
        # is connecting to this database will receive a "database gone" event from the
        # old PostgreSQL library (ops-lib-pgsql) and the connection between the
        # application and this charm will not work.
        for databag in [application_relation_databag, unit_relation_databag]:
            # This list of subnets is not being filled correctly yet.
            databag["allowed-subnets"] = self._get_allowed_subnets(event.relation)
            databag["allowed-units"] = self._get_allowed_units(event.relation)
            databag["host"] = f"{hostname}"
            databag["master"] = primary
            databag["port"] = "5432"
            databag["standbys"] = standbys
            databag["state"] = "standalone"  # "master"
            databag["version"] = "12"
            databag["user"] = user
            databag["password"] = password
            databag["database"] = database

    def _get_allowed_units(self, relation: Relation) -> str:
        return ",".join(
            sorted(
                unit.name
                for unit in relation.data
                if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name)
            )
        )

    def _get_allowed_subnets(self, relation: Relation) -> str:
        def _comma_split(s) -> Iterable[str]:
            if s:
                for b in s.split(","):
                    b = b.strip()
                    if b:
                        yield b

        subnets = set()
        for unit, reldata in relation.data.items():
            logger.warning(f"Checking subnets for {unit}")
            logger.warning(reldata)
            if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name):
                # NB. egress-subnets is not always available.
                subnets.update(set(_comma_split(reldata.get("egress-subnets", ""))))
        return ",".join(sorted(subnets))

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the departure of legacy db and db-admin relations.

        Remove unit name from allowed_units key.
        """
        if not self._charm.unit.is_leader():
            return

        if event.departing_unit.app == self._charm.app:
            # Just run for departing of remote units.
            return

        departing_unit = event.departing_unit.name
        local_unit_data = event.relation.data[self._charm.unit]
        local_app_data = event.relation.data[self._charm.app]

        current_allowed_units = local_unit_data.get("allowed_units", "")

        logger.debug(f"Removing unit {departing_unit} from allowed_units")
        local_app_data["allowed_units"] = local_unit_data["allowed_units"] = " ".join(
            {unit for unit in current_allowed_units.split() if unit != departing_unit}
        )

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        # Remove the user created for this relation.
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self._charm._peers.data[self._charm.app]
            or not self._charm._patroni.member_started
        ):
            event.defer()
            return

        if not self._charm.unit.is_leader():
            return

        application_relation_databag = event.relation.data[self._charm.app]
        database = application_relation_databag.get("database")

        # Connect to the PostgreSQL instance to later create a user and the database.
        hostname = self._charm._get_hostname_from_unit(self._charm._patroni.get_primary())
        connection = connect_to_database(
            database, "postgres", hostname, self._charm._get_postgres_password()
        )

        # Drop the user.
        user = f"relation_id_{event.relation.id}"
        drop_user(connection, user)
