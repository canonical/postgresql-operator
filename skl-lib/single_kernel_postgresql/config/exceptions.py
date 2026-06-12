#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific exceptions."""

from single_kernel_postgresql.compat.postgresql import PostgreSQLBaseError


class PostgreSQLFileOperationError(PostgreSQLBaseError):
    """Exception thrown when file operations related to PostgreSQL fail."""
