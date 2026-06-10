#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for Charm."""

import logging
from typing import Any

from data_platform_helpers.advanced_statuses import StatusObject

logger = logging.getLogger(__name__)


def format_status(status: StatusObject, params: dict[str, Any] | None) -> StatusObject:
    """Get the copy of the status object with the message formatted to params.

    If params are empty, returns original status.
    """
    if params is None:
        return status

    class SafeDict(dict):
        def __missing__(self, key):
            return "{}"

    return StatusObject(
        status=status.status,
        message=status.message.format_map(SafeDict(params)),
        running=status.running,
        approved_critical_component=status.approved_critical_component,
    )
