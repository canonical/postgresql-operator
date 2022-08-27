# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

DATABASE = "database"
DATABASE_PORT = "5432"
LEGACY_DB = "db"
LEGACY_DB_ADMIN = "db-admin"
PEER = "database-peers"
ALL_CLIENT_RELATIONS = [DATABASE, LEGACY_DB, LEGACY_DB_ADMIN]
REPLICATION_USER = "replication"
REPLICATION_PASSWORD_KEY = "replication-password"
USER = "operator"
USER_PASSWORD_KEY = "operator-password"
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [REPLICATION_USER, USER]
