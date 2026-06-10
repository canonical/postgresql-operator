# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Statuses for the PostgreSQL Charm.

This module defines various status enums that represent the state of the charm.
"""

from enum import Enum

from data_platform_helpers.advanced_statuses import StatusObject


class GeneralStatuses(Enum):
    """Collection of common charm statuses."""

    ACTIVE_IDLE = StatusObject(status="active", message="")
    MAINTAINENANCE_INSTALLING = StatusObject(status="maintenance", message="installing PostgreSQL")
    WAITING_POSTGRESQL_START = StatusObject(
        status="waiting", message="waiting to start PostgreSQL"
    )
    FAILED_SETTING_PASSWORDS = StatusObject(
        status="blocked", message="Password setting for system users failed."
    )
    WAITING_PASSWORDS_GENERATION = StatusObject(
        status="waiting", message="awaiting passwords generation"
    )
    WAITING_DATABASE_TO_START = StatusObject(
        status="waiting", message="awaiting for database to start"
    )


# Manager specific statuses
# TODO: populate with actual statuses as we implement the managers.
class TlsStatuses(Enum):
    """Collection of charm statuses related to tls manager."""

    TLS_RELATION_MISSING = StatusObject(
        status="blocked", message="Missing TLS relation with this cluster."
    )


class PatroniStatuses(Enum):
    """Collection of charm statuses related to Patroni manager."""

    FAILLED_STARTING_PATRONI = StatusObject(status="blocked", message="failed to start Patroni")
    WAITING_MEMBER_START = StatusObject(status="waiting", message="awaiting for member to start")
