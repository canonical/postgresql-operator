# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

MONITORING_SNAP_SERVICE = "prometheus-postgres-exporter"
PGBACKREST_MONITORING_SNAP_SERVICE = "pgbackrest-exporter"
PATRONI_SERVICE_NAME = "snap.charmed-postgresql.patroni.service"
PATRONI_SERVICE_DEFAULT_PATH = f"/etc/systemd/system/{PATRONI_SERVICE_NAME}"

# Snap constants.
PGBACKREST_EXECUTABLE = "charmed-postgresql.pgbackrest"

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

RAFT_PORT = 2222
RAFT_PARTNER_PREFIX = "partner_node_status_server_"
