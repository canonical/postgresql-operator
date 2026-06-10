#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for PostgreSQL."""

from ops.main import main
from single_kernel_postgresql.charms.k8s_charm import PostgreSQLK8sCharm

if __name__ == "__main__":
    main(PostgreSQLK8sCharm)
