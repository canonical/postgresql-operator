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


# Manager specific statuses
# TODO: populate with actual statuses as we implement the managers.
class TlsStatuses(Enum):
    """Collection of charm statuses related to tls manager."""

    TLS_RELATION_MISSING = StatusObject(
        status="blocked", message="Missing TLS relation with this cluster."
    )
