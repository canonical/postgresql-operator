# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Background process for rotating logs."""

import logging
import os
import subprocess
from time import sleep

from ops.charm import CharmBase
from ops.framework import Object
from ops.model import ActiveStatus

from constants import PGBACKREST_LOGROTATE_FILE

logger = logging.getLogger(__name__)

# File path for the spawned rotate logs process to write logs.
LOG_FILE_PATH = "/var/log/rotate_logs.log"


class RotateLogs(Object):
    """Rotate logs every minute."""

    def __init__(self, charm: CharmBase):
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
            ["/usr/bin/python3", "src/rotate_logs.py"],
            stdout=open(LOG_FILE_PATH, "a"),
            stderr=subprocess.STDOUT,
        ).pid

        self._charm.unit_peer_data.update({"rotate-logs-pid": f"{pid}"})
        logging.info("Started rotate logs process with PID {}".format(pid))


def main():
    """Main loop that calls logrotate."""
    while True:
        subprocess.run(["logrotate", "-f", PGBACKREST_LOGROTATE_FILE])

        # Wait 60 seconds before executing logrotate again.
        sleep(60)


if __name__ == "__main__":
    main()
