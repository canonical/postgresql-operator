# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Literal string for the different charms.

This module should contain the literals used in the charms (paths, relation names, etc).
"""

# Permissions.
POSTGRESQL_STORAGE_PERMISSIONS = 0o700

# Relations
PEER_RELATION = "database-peers"
STATUS_PEERS_RELATION = "status-peers"

# Users.
BACKUP_USER = "backup"
MONITORING_USER = "monitoring"
REPLICATION_USER = "replication"
REWIND_USER = "rewind"
SNAP_USER = "_daemon_"
USER = "operator"
SYSTEM_USERS = [MONITORING_USER, REPLICATION_USER, REWIND_USER, USER]

# File permissions as octal
# standard directory permissions
DIR_PERMISSIONS_READONLY = 0o750


# Container name for K8s deployments
CONTAINER_NAME = "postgresql"

API_REQUEST_TIMEOUT = 5


# --- Shared constants migrated from the charms ---

# Database
DATABASE = "database"
DATABASE_DEFAULT_NAME = "postgres"
DATABASE_PORT = "5432"

# TLS files
TLS_KEY_FILE = "key.pem"
TLS_CA_FILE = "ca.pem"
TLS_CERT_FILE = "cert.pem"

# Metrics ports (kept as str to match the K8s charm; VM adapts on flip)
METRICS_PORT = "9187"
PGBACKREST_METRICS_PORT = "9854"

# Secret/database mapping labels
USERNAME_MAPPING_LABEL = "custom-usernames"
DATABASE_MAPPING_LABEL = "prefix-databases"

# Overrides
BACKUP_TYPE_OVERRIDES = {"full": "full", "differential": "diff", "incremental": "incr"}
PLUGIN_OVERRIDES = {"audit": "pgaudit", "uuid_ossp": '"uuid-ossp"'}

# SPI extension modules
SPI_MODULE = ["refint", "autoinc", "insert_username", "moddatetime"]

# Tracing
TRACING_RELATION_NAME = "tracing"

# Patroni
PATRONI_CLUSTER_STATUS_ENDPOINT = "cluster"

# pgBackRest
PGBACKREST_LOGROTATE_FILE = "/etc/logrotate.d/pgbackrest.logrotate"


# Scopes
APP_SCOPE = "app"
UNIT_SCOPE = "unit"

# Password keys
USER_PASSWORD_KEY = "operator-password"  # noqa: S105
MONITORING_PASSWORD_KEY = "monitoring-password"  # noqa: S105
REPLICATION_PASSWORD_KEY = "replication-password"  # noqa: S105
REWIND_PASSWORD_KEY = "rewind-password"  # noqa: S105
PATRONI_PASSWORD_KEY = "patroni-password"  # noqa: S105

# Secret labels
SECRET_INTERNAL_LABEL = "internal-secret"  # noqa: S105
SECRET_DELETED_LABEL = "None"  # noqa: S105
SECRET_KEY_OVERRIDES = {"ca": "cauth"}
SYSTEM_USERS_PASSWORD_CONFIG = "system-users"  # noqa: S105
