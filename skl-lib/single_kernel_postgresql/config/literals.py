# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Literal string for the different charms.

This module should contain the literals used in the charms (paths, relation names, etc).
"""

from typing import Literal

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

# Paths
## VM Paths
BASE_SNAP_DIR = "/var/snap/charmed-postgresql"
SNAP_DATA = "current"
SNAP_COMMON = "common"
SNAP = "/snap/charmed-postgresql/current"
VM_DATA_PATH = "var/lib/postgresql"

## K8s Paths
K8S_DATA_PATH = "var/lib/pg/data"

## Shared Paths
# NOTE: The paths don't have leading slahes since pathops
# will handle path concatenation otherwise it will use "/var/lib/postgresql"
# instead of using the root path or any other part defined by the / operator.
# e.g. snap_current / "/etc/postgresql" will result in "/etc/postgresql" instead of "/var/snap/postgresql/current/etc/postgresql"
POSTGRESQL_CONF_PATH = "etc/postgresql"
POSTGRESQL_CONF_FILE = "postgresql.conf"


## TLS Paths
TLS_CA_BUNDLE_FILE = "peer_ca_bundle.pem"

# Scopes
SCOPES = Literal["app", "unit"]
APP_SCOPE = "app"
UNIT_SCOPE = "unit"

# Patroni
## Patroni Paths
PATRONI_CONF_PATH = "etc/patroni"
## Patroni states
STARTED_STATES = ["running", "streaming"]
RUNNING_STATES = [*STARTED_STATES, "starting"]
## Patroni config
ORIGINAL_PATRONI_ON_FAILURE_CONDITION = "restart"


# Secrets
SECRET_KEY_OVERRIDES = {"ca": "cauth"}


# Password keys
REPLICATION_PASSWORD_KEY = "replication-password"  # noqa: S105
REWIND_PASSWORD_KEY = "rewind-password"  # noqa: S105
USER_PASSWORD_KEY = "operator-password"  # noqa: S105
MONITORING_PASSWORD_KEY = "monitoring-password"  # noqa: S105
RAFT_PASSWORD_KEY = "raft-password"  # noqa: S105
PATRONI_PASSWORD_KEY = "patroni-password"  # noqa: S105
SECRET_INTERNAL_LABEL = "internal-secret"  # noqa: S105
SECRET_DELETED_LABEL = "None"  # noqa: S105
SYSTEM_USERS_PASSWORD_CONFIG = "system-users"  # noqa: S105

# K8s
## K8s Services
K8S_POSTGRESQL_SERVICE_NAME = "postgresql"
K8S_PGBACK_REST_SERVER_SERVICE_NAME = "pgbackrest server"
K8S_LDAP_SYNC_SERVICE_NAME = "ldap-sync"
K8S_METRICS_SERVER_SERVICE_NAME = "metrics_server"
K8S_PGBACKREST_METRICS_SERVER_SERVICE_NAME = "pgbackrest_metrics_server"
K8S_ROTATE_LOGS_SERVICE_NAME = "rotate-logs"
## K8s User and group
K8S_WORKLOAD_OS_GROUP = "postgres"
K8S_WORKLOAD_OS_USER = "postgres"


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
