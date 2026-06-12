# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

from single_kernel_postgresql.config.literals import (  # noqa: F401
    APP_SCOPE,
    BACKUP_TYPE_OVERRIDES,
    BACKUP_USER,
    DATABASE,
    DATABASE_DEFAULT_NAME,
    DATABASE_MAPPING_LABEL,
    DATABASE_PORT,
    METRICS_PORT,
    MONITORING_PASSWORD_KEY,
    PATRONI_CLUSTER_STATUS_ENDPOINT,
    PATRONI_PASSWORD_KEY,
    PEER_RELATION,
    PGBACKREST_LOGROTATE_FILE,
    PGBACKREST_METRICS_PORT,
    PLUGIN_OVERRIDES,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    SECRET_DELETED_LABEL,
    SECRET_INTERNAL_LABEL,
    SECRET_KEY_OVERRIDES,
    SNAP_USER,
    SPI_MODULE,
    SYSTEM_USERS_PASSWORD_CONFIG,
    TLS_CA_FILE,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    TRACING_RELATION_NAME,
    UNIT_SCOPE,
    USER_PASSWORD_KEY,
    USERNAME_MAPPING_LABEL,
)

BACKUP_ID_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PGBACKREST_BACKUP_ID_FORMAT = "%Y%m%d-%H%M%S"
ALL_CLIENT_RELATIONS = [DATABASE]
REPLICATION_CONSUMER_RELATION = "replication"
REPLICATION_OFFER_RELATION = "replication-offer"
TLS_CA_BUNDLE_FILE = "peer_ca_bundle.pem"
MONITORING_SNAP_SERVICE = "prometheus-postgres-exporter"
PGBACKREST_MONITORING_SNAP_SERVICE = "pgbackrest-exporter"
PATRONI_SERVICE_NAME = "snap.charmed-postgresql.patroni.service"
PATRONI_SERVICE_DEFAULT_PATH = f"/etc/systemd/system/{PATRONI_SERVICE_NAME}"

# Snap constants.
PGBACKREST_EXECUTABLE = "charmed-postgresql.pgbackrest"
# pgBackRest logging configuration
# We use stderr for all error/warning output to have a consistent, predictable error extraction
# mechanism. By default, pgBackRest uses stdout (console) for warnings, but we standardize on
# stderr to avoid potential log duplication and to make error handling more reliable.
# Reference: https://pgbackrest.org/configuration.html#section-log
PGBACKREST_LOG_LEVEL_STDERR = "--log-level-stderr=warn"
# pgBackRest error codes
PGBACKREST_ARCHIVE_TIMEOUT_ERROR_CODE = (
    82  # Archive timeout - unable to archive WAL files within configured timeout period
)

SNAP_COMMON_PATH = "/var/snap/charmed-postgresql/common"
SNAP_CURRENT_PATH = "/var/snap/charmed-postgresql/current"
DATA_DIR_SUBFOLDER = "16/main"

SNAP_CONF_PATH = f"{SNAP_CURRENT_PATH}/etc"
SNAP_DATA_PATH = f"{SNAP_COMMON_PATH}/var/lib"
SNAP_LOGS_PATH = f"{SNAP_COMMON_PATH}/var/log"
ARCHIVE_STORAGE_PATH = f"{SNAP_COMMON_PATH}/data/archive"
LOGS_STORAGE_PATH = f"{SNAP_COMMON_PATH}/data/logs"
TEMP_STORAGE_PATH = f"{SNAP_COMMON_PATH}/data/temp"

PATRONI_CONF_PATH = f"{SNAP_CONF_PATH}/patroni"
PATRONI_LOGS_PATH = f"{SNAP_LOGS_PATH}/patroni"

PGBACKREST_CONF_PATH = f"{SNAP_CONF_PATH}/pgbackrest"
PGBACKREST_LOGS_PATH = f"{SNAP_LOGS_PATH}/pgbackrest"

POSTGRESQL_CONF_PATH = f"{SNAP_CONF_PATH}/postgresql"
POSTGRESQL_DATA_PATH = f"{SNAP_DATA_PATH}/postgresql"
POSTGRESQL_DATA_DIR = f"{POSTGRESQL_DATA_PATH}/{DATA_DIR_SUBFOLDER}"
ARCHIVE_DATA_DIR = f"{ARCHIVE_STORAGE_PATH}/{DATA_DIR_SUBFOLDER}"
LOGS_DATA_DIR = f"{LOGS_STORAGE_PATH}/{DATA_DIR_SUBFOLDER}"
TEMP_DATA_DIR = f"{TEMP_STORAGE_PATH}/{DATA_DIR_SUBFOLDER}"
POSTGRESQL_LOGS_PATH = f"{SNAP_LOGS_PATH}/postgresql"

UPDATE_CERTS_BIN_PATH = "/usr/sbin/update-ca-certificates"

PGBACKREST_CONFIGURATION_FILE = f"--config={PGBACKREST_CONF_PATH}/pgbackrest.conf"

# VM-only password key (not in the shared lib)
RAFT_PASSWORD_KEY = "raft-password"  # noqa: S105

TRACING_PROTOCOL = "otlp_http"

# Watcher constants
WATCHER_OFFER_RELATION = "watcher-offer"
WATCHER_RELATION = "watcher"
WATCHER_USER = "watcher"

# Labels are not confidential
WATCHER_PASSWORD_KEY = "watcher-password"  # noqa: S105
WATCHER_SECRET_LABEL = "watcher-secret"  # noqa: S105

RAFT_PORT = 2222
RAFT_PARTNER_PREFIX = "partner_node_status_server_"
