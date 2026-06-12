#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cluster Manager.

Responsible for managing cluster-wide operations.
"""

import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope

from single_kernel_postgresql.compat.postgresql import PostgreSQLBase as PostgreSQLClient
from single_kernel_postgresql.config.statuses import GeneralStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.managers.base import BaseManager
from single_kernel_postgresql.workload.base import BaseWorkload

logger = logging.getLogger(__name__)


class ClusterManager(BaseManager):
    """PostgreSQL Cluster Manager.

    This manager is responsible for handling cluster-wide operations.
    """

    def __init__(self, state: CharmState, workload: BaseWorkload, client: PostgreSQLClient):
        super().__init__(state, workload, "cluster_manager", client)

    def get_statuses(
        self, scope: AdvancedStatusesScope, recompute: bool = False
    ) -> list[StatusObject]:
        """Compute the manager's statuses."""
        return [GeneralStatuses.ACTIVE_IDLE.value]
