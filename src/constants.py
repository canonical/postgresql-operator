# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

BACKUP_ID_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PGBACKREST_BACKUP_ID_FORMAT = "%Y%m%d-%H%M%S"
DATABASE = "database"
DATABASE_PORT = "5432"
LEGACY_DB = "db"
LEGACY_DB_ADMIN = "db-admin"
PEER = "database-peers"
ALL_CLIENT_RELATIONS = [DATABASE, LEGACY_DB, LEGACY_DB_ADMIN]
API_REQUEST_TIMEOUT = 5
PATRONI_CLUSTER_STATUS_ENDPOINT = "cluster"
BACKUP_USER = "backup"
REPLICATION_USER = "replication"
REPLICATION_PASSWORD_KEY = "replication-password"
REWIND_USER = "rewind"
REWIND_PASSWORD_KEY = "rewind-password"
TLS_KEY_FILE = "key.pem"
TLS_CA_FILE = "ca.pem"
TLS_CERT_FILE = "cert.pem"
USER = "operator"
USER_PASSWORD_KEY = "operator-password"
MONITORING_USER = "monitoring"
MONITORING_PASSWORD_KEY = "monitoring-password"
MONITORING_SNAP_SERVICE = "prometheus-postgres-exporter"
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [BACKUP_USER, REPLICATION_USER, REWIND_USER, USER, MONITORING_USER]

# Snap constants.
PGBACKREST_EXECUTABLE = "charmed-postgresql.pgbackrest"
POSTGRESQL_SNAP_NAME = "charmed-postgresql"
SNAP_PACKAGES = [
    (
        POSTGRESQL_SNAP_NAME,
        {"revision": {"aarch64": "110", "x86_64": "111"}, "channel": "14/stable"},
    )
]

SNAP_COMMON_PATH = "/var/snap/charmed-postgresql/common"
SNAP_CURRENT_PATH = "/var/snap/charmed-postgresql/current"

SNAP_CONF_PATH = f"{SNAP_CURRENT_PATH}/etc"
SNAP_DATA_PATH = f"{SNAP_COMMON_PATH}/var/lib"
SNAP_LOGS_PATH = f"{SNAP_COMMON_PATH}/var/log"

PATRONI_CONF_PATH = f"{SNAP_CONF_PATH}/patroni"
PATRONI_LOGS_PATH = f"{SNAP_LOGS_PATH}/patroni"

PGBACKREST_CONF_PATH = f"{SNAP_CONF_PATH}/pgbackrest"
PGBACKREST_LOGS_PATH = f"{SNAP_LOGS_PATH}/pgbackrest"

POSTGRESQL_CONF_PATH = f"{SNAP_CONF_PATH}/postgresql"
POSTGRESQL_DATA_PATH = f"{SNAP_DATA_PATH}/postgresql"
POSTGRESQL_LOGS_PATH = f"{SNAP_LOGS_PATH}/postgresql"

PGBACKREST_CONFIGURATION_FILE = f"--config={PGBACKREST_CONF_PATH}/pgbackrest.conf"

METRICS_PORT = "9187"

SECRET_INTERNAL_LABEL = "internal-secret"
SECRET_DELETED_LABEL = "None"

APP_SCOPE = "app"
UNIT_SCOPE = "unit"

SECRET_KEY_OVERRIDES = {"ca": "cauth"}
