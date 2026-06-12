#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base PostgreSQL manager."""

import logging

from data_platform_helpers.advanced_statuses import ManagerStatusProtocol, StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope

from single_kernel_postgresql.compat.postgresql import PostgreSQLBase as PostgreSQLClient
from single_kernel_postgresql.config.statuses import GeneralStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.workload.base import BaseWorkload

logger = logging.getLogger(__name__)


class BaseManager(ManagerStatusProtocol):
    """Base PostgreSQL Manager.

    Include a set of functions and properties useful to other managers.
    """

    def __init__(
        self, state: CharmState, workload: BaseWorkload, name: str, client: PostgreSQLClient
    ):
        self.state: CharmState = state  # type: ignore[override]
        self.workload = workload
        self.name = name
        self.postgresql_client = client

    def get_statuses(
        self, scope: AdvancedStatusesScope, recompute: bool = False
    ) -> list[StatusObject]:
        """Compute the manager's statuses."""
        # TODO: Implement actual status computation logic for each manager
        # It is preferred to not have the recompute check and do recompute status
        # after each hook.
        if not recompute:
            return self.state.statuses.get(scope, self.name).root or [
                GeneralStatuses.ACTIVE_IDLE.value
            ]

        return [GeneralStatuses.ACTIVE_IDLE.value]
