#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Application charm that connects to database charms.

This charm is meant to be used only for testing
of the libraries in this repository.
"""

import json
import logging
import os
import signal
import subprocess
from typing import Dict, Optional

import ops.lib
import psycopg2
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseEndpointsChangedEvent,
    DatabaseRequires,
)
from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus, Relation
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

pgsql = ops.lib.use("pgsql", 1, "postgresql-charmers@lists.launchpad.net")

# Extra roles that this application needs when interacting with the database.
EXTRA_USER_ROLES = "CREATEDB,CREATEROLE"

PEER = "postgresql-test-peers"
LAST_WRITTEN_FILE = "/tmp/last_written_value"
CONFIG_FILE = "/tmp/continuous_writes_config"
PROC_PID_KEY = "proc-pid"


class ApplicationCharm(CharmBase):
    """Application charm that connects to database charms."""

    @property
    def _peers(self) -> Optional[Relation]:
        """Retrieve the peer relation (`ops.model.Relation`)."""
        return self.model.get_relation(PEER)

    @property
    def app_peer_data(self) -> Dict:
        """Application peer relation data object."""
        if self._peers is None:
            return {}

        return self._peers.data[self.app]

    def __init__(self, *args):
        super().__init__(*args)

        # Default charm events.
        self.framework.observe(self.on.start, self._on_start)

        # Events related to the first database that is requested
        # (these events are defined in the database requires charm library).
        self.first_database_name = f'{self.app.name.replace("-", "_")}_first_database'
        self.first_database = DatabaseRequires(
            self, "first-database", self.first_database_name, EXTRA_USER_ROLES
        )
        self.framework.observe(
            self.first_database.on.database_created, self._on_first_database_created
        )
        self.framework.observe(
            self.first_database.on.endpoints_changed, self._on_first_database_endpoints_changed
        )
        self.framework.observe(
            self.on.clear_continuous_writes_action, self._on_clear_continuous_writes_action
        )
        self.framework.observe(
            self.on.start_continuous_writes_action, self._on_start_continuous_writes_action
        )
        self.framework.observe(
            self.on.stop_continuous_writes_action, self._on_stop_continuous_writes_action
        )

        # Events related to the second database that is requested
        # (these events are defined in the database requires charm library).
        database_name = f'{self.app.name.replace("-", "_")}_second_database'
        self.second_database = DatabaseRequires(
            self, "second-database", database_name, EXTRA_USER_ROLES
        )
        self.framework.observe(
            self.second_database.on.database_created, self._on_second_database_created
        )
        self.framework.observe(
            self.second_database.on.endpoints_changed, self._on_second_database_endpoints_changed
        )

        # Multiple database clusters charm events (clusters/relations without alias).
        database_name = f'{self.app.name.replace("-", "_")}_multiple_database_clusters'
        self.database_clusters = DatabaseRequires(
            self, "multiple-database-clusters", database_name, EXTRA_USER_ROLES
        )
        self.framework.observe(
            self.database_clusters.on.database_created, self._on_cluster_database_created
        )
        self.framework.observe(
            self.database_clusters.on.endpoints_changed,
            self._on_cluster_endpoints_changed,
        )

        # Multiple database clusters charm events (defined dynamically
        # in the database requires charm library, using the provided cluster/relation aliases).
        database_name = f'{self.app.name.replace("-", "_")}_aliased_multiple_database_clusters'
        cluster_aliases = ["cluster1", "cluster2"]  # Aliases for the multiple clusters/relations.
        self.aliased_database_clusters = DatabaseRequires(
            self,
            "aliased-multiple-database-clusters",
            database_name,
            EXTRA_USER_ROLES,
            cluster_aliases,
        )
        # Each database cluster will have its own events
        # with the name having the cluster/relation alias as the prefix.
        self.framework.observe(
            self.aliased_database_clusters.on.cluster1_database_created,
            self._on_cluster1_database_created,
        )
        self.framework.observe(
            self.aliased_database_clusters.on.cluster1_endpoints_changed,
            self._on_cluster1_endpoints_changed,
        )
        self.framework.observe(
            self.aliased_database_clusters.on.cluster2_database_created,
            self._on_cluster2_database_created,
        )
        self.framework.observe(
            self.aliased_database_clusters.on.cluster2_endpoints_changed,
            self._on_cluster2_endpoints_changed,
        )

        # Relation used to test the situation where no database name is provided.
        self.no_database = DatabaseRequires(self, "no-database", database_name="")

        # Legacy interface
        self.db = pgsql.PostgreSQLClient(self, "db")
        self.framework.observe(
            self.db.on.database_relation_joined, self._on_database_relation_joined
        )

        self.framework.observe(self.on.run_sql_action, self._on_run_sql_action)
        self.framework.observe(self.on.test_tls_action, self._on_test_tls_action)

    def _on_start(self, _) -> None:
        """Only sets an Active status."""
        self.unit.status = ActiveStatus()

    # First database events observers.
    def _on_first_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event triggered when a database was created for this application."""
        # Retrieve the credentials using the charm library.
        logger.info(f"first database credentials: {event.username} {event.password}")
        self.unit.status = ActiveStatus("received database credentials of the first database")

    def _on_first_database_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        logger.info(f"first database endpoints have been changed to: {event.endpoints}")
        if self._connection_string is None:
            return

        if not self.app_peer_data.get(PROC_PID_KEY):
            return None

        with open(CONFIG_FILE, "w") as fd:
            fd.write(self._connection_string)
            os.fsync(fd)

        try:
            os.kill(int(self.app_peer_data[PROC_PID_KEY]), signal.SIGKILL)
        except ProcessLookupError:
            del self.app_peer_data[PROC_PID_KEY]
            return
        count = self._count_writes()
        self._start_continuous_writes(count + 1)

    # Second database events observers.
    def _on_second_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event triggered when a database was created for this application."""
        # Retrieve the credentials using the charm library.
        logger.info(f"second database credentials: {event.username} {event.password}")
        self.unit.status = ActiveStatus("received database credentials of the second database")

    def _on_second_database_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        logger.info(f"second database endpoints have been changed to: {event.endpoints}")

    # Multiple database clusters events observers.
    def _on_cluster_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event triggered when a database was created for this application."""
        # Retrieve the credentials using the charm library.
        logger.info(
            f"cluster {event.relation.app.name} credentials: {event.username} {event.password}"
        )
        self.unit.status = ActiveStatus(
            f"received database credentials for cluster {event.relation.app.name}"
        )

    def _on_cluster_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        logger.info(
            f"cluster {event.relation.app.name} endpoints have been changed to: {event.endpoints}"
        )

    # Multiple database clusters events observers (for aliased clusters/relations).
    def _on_cluster1_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event triggered when a database was created for this application."""
        # Retrieve the credentials using the charm library.
        logger.info(f"cluster1 credentials: {event.username} {event.password}")
        self.unit.status = ActiveStatus("received database credentials for cluster1")

    def _on_cluster1_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        logger.info(f"cluster1 endpoints have been changed to: {event.endpoints}")

    def _on_cluster2_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event triggered when a database was created for this application."""
        # Retrieve the credentials using the charm library.
        logger.info(f"cluster2 credentials: {event.username} {event.password}")
        self.unit.status = ActiveStatus("received database credentials for cluster2")

    def _on_cluster2_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        logger.info(f"cluster2 endpoints have been changed to: {event.endpoints}")

    # HA event observers
    @property
    def _connection_string(self) -> Optional[str]:
        """Returns the PostgreSQL connection string."""
        data = list(self.first_database.fetch_relation_data().values())[0]
        username = data.get("username")
        password = data.get("password")
        endpoints = data.get("endpoints")
        if None in [username, password, endpoints]:
            return None

        host = endpoints.split(":")[0]

        if not host or host == "None":
            return None

        return (
            f"dbname='{self.first_database_name}' user='{username}'"
            f" host='{host}' password='{password}' connect_timeout=5"
        )

    def _count_writes(self) -> int:
        """Count the number of records in the continuous_writes table."""
        with psycopg2.connect(
            self._connection_string
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
            count = cursor.fetchone()[0]
        connection.close()
        return count

    def _on_clear_continuous_writes_action(self, event: ActionEvent) -> None:
        """Clears database writes."""
        if self._connection_string is None:
            event.set_results({"result": "False"})
            return

        try:
            self._stop_continuous_writes()
        except Exception as e:
            event.set_results({"result": "False"})
            logger.exception("Unable to stop writes to drop table", exc_info=e)
            return

        try:
            with psycopg2.connect(
                self._connection_string
            ) as connection, connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS continuous_writes;")
            event.set_results({"result": "True"})
        except Exception as e:
            event.set_results({"result": "False"})
            logger.exception("Unable to drop table", exc_info=e)
        finally:
            connection.close()

    def _on_start_continuous_writes_action(self, event: ActionEvent) -> None:
        """Start the continuous writes process."""
        if self._connection_string is None:
            event.set_results({"result": "False"})
            return

        try:
            self._stop_continuous_writes()
        except Exception as e:
            event.set_results({"result": "False"})
            logger.exception("Unable to stop writes to create table", exc_info=e)
            return

        try:
            # Create the table to write records on and also a unique index to prevent duplicate
            # writes.
            with psycopg2.connect(
                self._connection_string
            ) as connection, connection.cursor() as cursor:
                connection.autocommit = True
                cursor.execute("CREATE TABLE IF NOT EXISTS continuous_writes(number INTEGER);")
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS number ON continuous_writes(number);"
                )
        except Exception as e:
            event.set_results({"result": "False"})
            logger.exception("Unable to create table", exc_info=e)
            return
        finally:
            connection.close()

        self._start_continuous_writes(1)
        event.set_results({"result": "True"})

    def _on_stop_continuous_writes_action(self, event: ActionEvent) -> None:
        """Stops the continuous writes process."""
        writes = self._stop_continuous_writes()
        event.set_results({"writes": writes})

    def _start_continuous_writes(self, starting_number: int) -> None:
        """Starts continuous writes to PostgreSQL instance."""
        if self._connection_string is None:
            return

        # Stop any writes that might be going.
        self._stop_continuous_writes()

        with open(CONFIG_FILE, "w") as fd:
            fd.write(self._connection_string)
            os.fsync(fd)

        # Run continuous writes in the background.
        popen = subprocess.Popen(
            [
                "/usr/bin/python3",
                "src/continuous_writes.py",
                str(starting_number),
            ]
        )

        # Store the continuous writes process ID to stop the process later.
        self.app_peer_data[PROC_PID_KEY] = str(popen.pid)

    def _stop_continuous_writes(self) -> Optional[int]:
        """Stops continuous writes to PostgreSQL and returns the last written value."""
        if not self.app_peer_data.get(PROC_PID_KEY):
            return None

        # Stop the process.
        try:
            os.kill(int(self.app_peer_data[PROC_PID_KEY]), signal.SIGTERM)
        except ProcessLookupError:
            del self.app_peer_data[PROC_PID_KEY]
            return None

        del self.app_peer_data[PROC_PID_KEY]

        # Return the max written value (or -1 if it was not possible to get that value).
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(5)):
                with attempt:
                    with open(LAST_WRITTEN_FILE, "r") as fd:
                        last_written_value = int(fd.read())
        except RetryError as e:
            logger.exception("Unable to read result", exc_info=e)
            return -1

        os.remove(LAST_WRITTEN_FILE)
        os.remove(CONFIG_FILE)
        return last_written_value

    # Legacy event handlers
    def _on_database_relation_joined(
        self, event: pgsql.DatabaseRelationJoinedEvent  # type: ignore
    ) -> None:
        """Handle db-relation-joined.

        Args:
            event: Event triggering the database relation joined handler.
        """
        if self.model.unit.is_leader():
            event.database = "db_with_extensions"
            event.extensions = ["pg_trgm:public", "unaccent:public"]

    # Run_sql action handler
    def _on_run_sql_action(self, event: ActionEvent):
        """An action that allows us to run SQL queries from this charm."""
        logger.info(event.params)

        relation_id = event.params["relation-id"]
        relation_name = event.params["relation-name"]
        if relation_name == self.first_database.relation_name:
            relation = self.first_database
        elif relation_name == self.second_database.relation_name:
            relation = self.second_database
        else:
            event.fail(message="invalid relation name")

        databag = relation.fetch_relation_data()[relation_id]

        dbname = event.params["dbname"]
        query = event.params["query"]
        user = databag.get("username")
        password = databag.get("password")

        if event.params["readonly"]:
            host = databag.get("read-only-endpoints").split(",")[0]
            dbname = f"{dbname}_readonly"
        else:
            host = databag.get("endpoints").split(",")[0]
        endpoint = host.split(":")[0]
        port = host.split(":")[1]

        logger.info(f"running query: \n{query}")
        connection = self.connect_to_database(
            database=dbname, user=user, password=password, host=endpoint, port=port
        )
        cursor = connection.cursor()
        cursor.execute(query)

        try:
            results = cursor.fetchall()
        except psycopg2.Error as error:
            results = [str(error)]
        logger.info(results)

        event.set_results({"results": json.dumps(results)})

    def _on_test_tls_action(self, event: ActionEvent):
        """An action that allows us to run SQL queries from this charm."""
        logger.info(event.params)

        relation_id = event.params["relation-id"]
        relation_name = event.params["relation-name"]
        if relation_name == self.first_database.relation_name:
            relation = self.first_database
        elif relation_name == self.second_database.relation_name:
            relation = self.second_database
        else:
            event.fail(message="invalid relation name")

        databag = relation.fetch_relation_data()[relation_id]

        dbname = event.params["dbname"]
        user = databag.get("username")
        password = databag.get("password")

        if event.params["readonly"]:
            host = databag.get("read-only-endpoints").split(",")[0]
            dbname = f"{dbname}_readonly"
        else:
            host = databag.get("endpoints").split(",")[0]
        endpoint = host.split(":")[0]
        port = host.split(":")[1]

        try:
            connection = self.connect_to_database(
                database=dbname, user=user, password=password, host=endpoint, port=port, tls=True
            )
            connection.close()
            event.set_results({"results": "True"})
        except psycopg2.OperationalError:
            event.set_results({"results": "False"})

    def connect_to_database(
        self, host: str, port: str, database: str, user: str, password: str, tls: bool = False
    ) -> psycopg2.extensions.connection:
        """Creates a psycopg2 connection object to the database, with autocommit enabled.

        Args:
            host: network host for the database
            port: port on which to access the database
            database: database to connect to
            user: user to use to connect to the database
            password: password for the given user
            tls: whether to require TLS

        Returns:
            psycopg2 connection object using the provided data
        """
        connstr = f"dbname='{database}' user='{user}' host='{host}' port='{port}' password='{password}' connect_timeout=1"
        if tls:
            connstr = f"{connstr} sslmode=require"
        logger.debug(f"connecting to database: \n{connstr}")
        connection = psycopg2.connect(connstr)
        connection.autocommit = True
        return connection


if __name__ == "__main__":
    main(ApplicationCharm)
