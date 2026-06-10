#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for PostgreSQL."""

from ops.main import main
from single_kernel_postgresql.charms.vm_charm import PostgreSQLVMCharm

if __name__ == "__main__":
    main(PostgreSQLVMCharm)
