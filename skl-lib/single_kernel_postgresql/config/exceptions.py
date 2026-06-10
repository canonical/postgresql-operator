#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific exceptions."""

from single_kernel_postgresql.compat.postgresql import PostgreSQLBaseError


class PostgreSQLFileOperationError(PostgreSQLBaseError):
    """Exception thrown when file operations related to PostgreSQL fail."""


class StorageUnavailableError(Exception):
    """Cannot find storage mountpoint."""


class SettingSystemPasswordError(PostgreSQLBaseError):
    """Exception thrown when setting the system password fails."""


class PostgreSQLCannotConnectError(Exception):
    """Cannot run smoke check on connected Database."""


class TlsError(Exception):
    """TLS implementation internal exception."""
