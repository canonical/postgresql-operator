#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Application charm that connects to database charms.

This charm is meant to be used only for testing
high availability of the PostgreSQL charm.
"""

import logging
import os
import signal
import subprocess
from typing import Dict, Optional

import psycopg2
from charms.data_platform_libs.v0.database_requires import DatabaseRequires
from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus, Relation
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

PEER = "application-peers"
LAST_WRITTEN_FILE = "/tmp/last_written_value"
CONFIG_FILE = "/tmp/continuous_writes_config"
PROC_PID_KEY = "proc-pid"


class ApplicationCharm(CharmBase):
    """Application charm that connects to PostgreSQL charm."""

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

        # Events related to the database that is requested.
        self.database_name = "application"
        self.database = DatabaseRequires(self, "database", self.database_name)
        self.framework.observe(self.database.on.endpoints_changed, self._on_endpoints_changed)
        self.framework.observe(
            self.on.clear_continuous_writes_action, self._on_clear_continuous_writes_action
        )
        self.framework.observe(
            self.on.start_continuous_writes_action, self._on_start_continuous_writes_action
        )
        self.framework.observe(
            self.on.stop_continuous_writes_action, self._on_stop_continuous_writes_action
        )

    @property
    def _connection_string(self) -> Optional[str]:
        """Returns the PostgreSQL connection string."""
        data = list(self.database.fetch_relation_data().values())[0]
        username = data.get("username")
        password = data.get("password")
        endpoints = data.get("endpoints")
        if None in [username, password, endpoints]:
            return None

        host = endpoints.split(":")[0]
        return (
            f"dbname='{self.database_name}' user='{username}'"
            f" host='{host}' password='{password}' connect_timeout=5"
        )

    def _on_start(self, _) -> None:
        """Only sets an Active status."""
        self.unit.status = ActiveStatus()

    def _on_endpoints_changed(self, _) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        if self._connection_string is None:
            return

        if not self.app_peer_data.get(PROC_PID_KEY):
            return None

        with open(CONFIG_FILE, "w") as fd:
            fd.write(self._connection_string)
            os.fsync(fd)

        try:
            os.kill(int(self.app_peer_data[PROC_PID_KEY]), signal.SIGHUP)
        except ProcessLookupError:
            del self.app_peer_data[PROC_PID_KEY]

    def _on_clear_continuous_writes_action(self, _) -> None:
        """Clears database writes."""
        if self._connection_string is None:
            return

        self._stop_continuous_writes()
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                with psycopg2.connect(
                    self._connection_string
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("DROP TABLE IF EXISTS continuous_writes;")
                connection.close()

    def _on_start_continuous_writes_action(self, _) -> None:
        """Start the continuous writes process."""
        self._start_continuous_writes(1)

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


if __name__ == "__main__":
    main(ApplicationCharm)
