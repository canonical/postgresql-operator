# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Background process for rotating logs."""

import subprocess
from time import sleep

PGBACKREST_LOGROTATE_FILE = "/etc/logrotate.d/pgbackrest.logrotate"


def main():
    """Main loop that calls logrotate."""
    while True:
        # Input is constant
        subprocess.run(["/usr/sbin/logrotate", "-f", PGBACKREST_LOGROTATE_FILE])  # noqa: S603

        # Wait 60 seconds before executing logrotate again.
        sleep(60)


if __name__ == "__main__":
    main()
