# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
from single_kernel_postgresql.config import literals


def test_shared_string_constants():
    assert literals.DATABASE == "database"
    assert literals.DATABASE_DEFAULT_NAME == "postgres"
    assert literals.DATABASE_PORT == "5432"
    assert literals.PATRONI_CLUSTER_STATUS_ENDPOINT == "cluster"
    assert literals.TLS_KEY_FILE == "key.pem"
    assert literals.TLS_CA_FILE == "ca.pem"
    assert literals.TLS_CERT_FILE == "cert.pem"
    assert literals.USERNAME_MAPPING_LABEL == "custom-usernames"
    assert literals.DATABASE_MAPPING_LABEL == "prefix-databases"
    assert literals.TRACING_RELATION_NAME == "tracing"
    assert literals.PGBACKREST_LOGROTATE_FILE == "/etc/logrotate.d/pgbackrest.logrotate"


def test_shared_collection_constants():
    assert literals.BACKUP_TYPE_OVERRIDES == {
        "full": "full",
        "differential": "diff",
        "incremental": "incr",
    }
    assert literals.PLUGIN_OVERRIDES == {"audit": "pgaudit", "uuid_ossp": '"uuid-ossp"'}
    assert literals.SPI_MODULE == ["refint", "autoinc", "insert_username", "moddatetime"]


def test_metrics_ports_are_str():
    assert literals.METRICS_PORT == "9187"
    assert literals.PGBACKREST_METRICS_PORT == "9854"
    assert isinstance(literals.METRICS_PORT, str)
    assert isinstance(literals.PGBACKREST_METRICS_PORT, str)


def test_scope_and_secret_constants():
    assert literals.APP_SCOPE == "app"
    assert literals.UNIT_SCOPE == "unit"
    assert literals.USER_PASSWORD_KEY == "operator-password"
    assert literals.MONITORING_PASSWORD_KEY == "monitoring-password"
    assert literals.REPLICATION_PASSWORD_KEY == "replication-password"
    assert literals.REWIND_PASSWORD_KEY == "rewind-password"
    assert literals.PATRONI_PASSWORD_KEY == "patroni-password"
    assert literals.SECRET_INTERNAL_LABEL == "internal-secret"
    assert literals.SECRET_DELETED_LABEL == "None"
    assert literals.SECRET_KEY_OVERRIDES == {"ca": "cauth"}
    assert literals.SYSTEM_USERS_PASSWORD_CONFIG == "system-users"
