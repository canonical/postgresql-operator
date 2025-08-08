# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Background process for rotating logs."""

import logging
import os
import subprocess
from typing import TYPE_CHECKING

from ops.framework import Object
from ops.model import ActiveStatus

from constants import PGBACKREST_LOGROTATE_FILE

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

# File path for the spawned rotate logs process to write logs.
LOG_FILE_PATH = "/var/log/rotate_logs.log"


class RotateLogs(Object):
    """Rotate logs every minute."""

    def __init__(self, charm: "PostgresqlOperatorCharm"):
        super().__init__(charm, "rotate-logs")
        self._charm = charm

    def start_log_rotation(self):
        """Start the rotate logs running in a new process."""
        if (
            not isinstance(self._charm.unit.status, ActiveStatus)
            or self._charm._peers is None
            or not os.path.exists(PGBACKREST_LOGROTATE_FILE)
        ):
            return
        if "rotate-logs-pid" in self._charm.unit_peer_data:
            # Double check that the PID exists.
            pid = int(self._charm.unit_peer_data["rotate-logs-pid"])
            try:
                os.kill(pid, 0)
                return
            except OSError:
                pass

        logging.info("Starting rotate logs process")

        pid = subprocess.Popen(
            ["/usr/bin/python3", "scripts/rotate_logs.py"],
            # File should not close
            stdout=open(LOG_FILE_PATH, "a"),  # noqa: SIM115
            stderr=subprocess.STDOUT,
        ).pid

        self._charm.unit_peer_data.update({"rotate-logs-pid": f"{pid}"})
        logging.info(f"Started rotate logs process with PID {pid}")
