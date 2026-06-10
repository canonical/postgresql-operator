#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Config Manager.

Responsible for managing the configuration of the PostgreSQL instance.
"""

import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope

from single_kernel_postgresql.config.literals import (
    POSTGRESQL_STORAGE_PERMISSIONS,
)
from single_kernel_postgresql.config.statuses import GeneralStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.managers.base import BaseManager
from single_kernel_postgresql.utils import _change_owner
from single_kernel_postgresql.utils.postgresql import PostgreSQL as PostgreSQLClient
from single_kernel_postgresql.workload.base import BaseWorkload

logger = logging.getLogger(__name__)


class ConfigManager(BaseManager):
    """PostgreSQL Config Manager.

    This manager is responsible for handling configuration operations.
    """

    def __init__(self, state: CharmState, workload: BaseWorkload, client: PostgreSQLClient):
        super().__init__(state, workload, "config_manager", client)

    def configure_patroni_on_unit(self):
        """Configure Patroni (configuration files and service) on the unit."""
        _change_owner(self.state.substrate, str(self.workload.paths.data))

        # Create empty base config
        self.workload.write_text("", self.workload.paths.postgresql_conf)

        # Expected permission
        # Replicas refuse to start with the default permissions
        self.workload.mkdir(
            self.workload.paths.data, mode=POSTGRESQL_STORAGE_PERMISSIONS, exist_ok=True
        )

    def get_statuses(
        self, scope: AdvancedStatusesScope, recompute: bool = False
    ) -> list[StatusObject]:
        """Compute the manager's statuses."""
        return [GeneralStatuses.ACTIVE_IDLE.value]
