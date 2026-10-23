# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for PostgreSQL."""

import logging

# First platform-specific import; on wrong architectures fall back to a
# blocked warning charm (the wheel for this arch is unavailable).
try:
    from single_kernel_postgresql.charms.vm_charm import PostgreSQLVMCharm
except ModuleNotFoundError:
    from ops.main import main
    from single_kernel_postgresql.utils.arch import (
        WrongArchitectureWarningCharm,
        is_wrong_architecture,
    )

    if is_wrong_architecture() and __name__ == "__main__":
        main(WrongArchitectureWarningCharm)
    raise

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(PostgreSQLVMCharm):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)


if __name__ == "__main__":
    from ops.main import main

    main(PostgresqlOperatorCharm)
