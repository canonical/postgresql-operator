#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cluster Manager.

Responsible for managing cluster-wide operations.
"""

import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope
from ops import ModelError, SecretNotFoundError
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.exceptions import (
    PostgreSQLCannotConnectError,
    SettingSystemPasswordError,
)
from single_kernel_postgresql.config.literals import (
    APP_SCOPE,
    MONITORING_PASSWORD_KEY,
    PATRONI_PASSWORD_KEY,
    RAFT_PASSWORD_KEY,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    USER_PASSWORD_KEY,
)
from single_kernel_postgresql.config.statuses import GeneralStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.managers.base import BaseManager
from single_kernel_postgresql.utils import new_password
from single_kernel_postgresql.utils.postgresql import PostgreSQL as PostgreSQLClient
from single_kernel_postgresql.workload.base import BaseWorkload
from single_kernel_postgresql.workload.vm import VMWorkload

logger = logging.getLogger(__name__)


class ClusterManager(BaseManager):
    """PostgreSQL Cluster Manager.

    This manager is responsible for handling cluster-wide operations.
    """

    def __init__(self, state: CharmState, workload: BaseWorkload, client: PostgreSQLClient):
        super().__init__(state, workload, "cluster_manager", client)

    def install_workload(self) -> None:
        """Install the workload."""
        if self.state.substrate == Substrates.VM and isinstance(self.workload, VMWorkload):
            self.workload.install_snap_package(revision=None)
            self.workload.create_snap_alias("patronictl")
            self.workload.create_snap_alias("psql")
        else:
            logger.debug(
                "No workload installation steps defined for substrate %s", self.state.substrate
            )

    def configure_system_passwords(self) -> None:
        """Configure system user passwords.

        This is called on leader units only to create system passwords
        if not already set.
        """
        # consider configured system user passwords
        raise_error = False
        system_user_passwords = {}
        if admin_secret_id := self.state.config.system_users:
            try:
                system_user_passwords = self.state.get_secret_from_id(secret_id=admin_secret_id)
            except (ModelError, SecretNotFoundError) as e:
                # only display the error but don't return to make sure all users have passwords
                logger.error(f"Error setting internal passwords: {e}")
                raise_error = True

        # The leader sets the needed passwords if they weren't set before.
        for key in (
            USER_PASSWORD_KEY,
            REPLICATION_PASSWORD_KEY,
            REWIND_PASSWORD_KEY,
            MONITORING_PASSWORD_KEY,
            RAFT_PASSWORD_KEY,
            PATRONI_PASSWORD_KEY,
        ):
            if self.state.get_secret(APP_SCOPE, key) is None:
                if key in system_user_passwords:
                    # use provided passwords for system-users if available
                    self.state.set_secret(APP_SCOPE, key, system_user_passwords[key])
                    logger.info(f"Using configured password for {key}")
                else:
                    # generate a password for this user if not provided
                    self.state.set_secret(APP_SCOPE, key, new_password())
                    logger.info(f"Generated new password for {key}")

        if raise_error:
            raise SettingSystemPasswordError("Failed to set system user passwords.")

    def expose_ip_and_port(self) -> None:
        """Expose the unit's IP and port to the peer relation."""
        self.state.peer.ip = self.state.unit_ip

        # Open port
        try:
            self.state.peer.unit.open_port("tcp", 5432)
        except ModelError:
            logger.exception("failed to open port")

    @property
    def can_connect_to_postgresql(self) -> bool:
        """Whether the local PostgreSQL instance is reachable and responding."""
        if not self.postgresql_client.password or not self.postgresql_client.current_host:
            return False
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    if not self.postgresql_client.get_postgresql_timezones():
                        logger.debug("Cannot connect to database (CannotConnectError)")
                        raise PostgreSQLCannotConnectError
        except RetryError:
            logger.debug("Cannot connect to database (RetryError)")
            return False
        return True

    def get_statuses(
        self, scope: AdvancedStatusesScope, recompute: bool = False
    ) -> list[StatusObject]:
        """Compute the manager's statuses."""
        if (
            not self.state.application.user_password
            or not self.state.application.replication_password
        ):
            return [GeneralStatuses.WAITING_PASSWORDS_GENERATION.value]

        if not self.can_connect_to_postgresql:
            return [GeneralStatuses.WAITING_DATABASE_TO_START.value]
        return [GeneralStatuses.ACTIVE_IDLE.value]
