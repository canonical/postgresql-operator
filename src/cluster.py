#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage cluster lifecycle."""

import logging
import shutil
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
from ops import BlockedStatus
from pysyncobj.utility import TcpUtility, UtilityException
from single_kernel_postgresql.config.exceptions import (
    AddRaftMemberFailedError,
    RaftNotPromotedError,
    RaftPostgresqlNotUpError,
    RaftPostgresqlStillUpError,
    RemoveRaftMemberFailedError,
)
from single_kernel_postgresql.config.literals import (
    PEER_RELATION,
)
from tenacity import (
    Retrying,
    wait_fixed,
)

from constants import (
    PATRONI_CONF_PATH,
    POSTGRESQL_CONF_PATH,
    RAFT_PARTNER_PREFIX,
    RAFT_PORT,
)

logger = logging.getLogger(__name__)

PG_BASE_CONF_PATH = f"{POSTGRESQL_CONF_PATH}/postgresql.conf"

STARTED_STATES = ["running", "streaming"]
RUNNING_STATES = [*STARTED_STATES, "starting"]

PATRONI_TIMEOUT = 10

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


class Patroni:
    """This class handles the bootstrap of a PostgreSQL database through Patroni."""

    def __init__(
        self,
        charm: "PostgresqlOperatorCharm",
        raft_password: str | None,
    ):
        """Initialize the Patroni class.

        Args:
            charm: PostgreSQL charm instance.
            raft_password: password for raft
        """
        self.charm = charm
        self.raft_password = raft_password

    def has_raft_quorum(self) -> bool:
        """Check if raft cluster has quorum."""
        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=self.raft_password, timeout=3)

        raft_host = "127.0.0.1:2222"
        try:
            raft_status = syncobj_util.executeCommand(raft_host, ["status"])
        except UtilityException:
            logger.warning("Has raft quorum: Cannot connect to raft cluster")
            return False
        if not raft_status:
            logger.warning("Has raft quorum: No status reported")
            return False
        return raft_status["has_quorum"]

    def remove_raft_data(self) -> None:
        """Stops Patroni and removes the raft journals."""
        logger.info("Stopping patroni")
        self.charm.patroni_manager.stop_patroni()

        logger.info("Wait for postgresql to stop")
        for attempt in Retrying(wait=wait_fixed(5)):
            with attempt:
                for proc in psutil.process_iter(["name"]):
                    if proc.name() == "postgres":
                        raise RaftPostgresqlStillUpError()

        logger.info("Removing raft data")
        try:
            path = Path(f"{PATRONI_CONF_PATH}/raft")
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
        except OSError as e:
            raise Exception(
                f"Failed to remove previous cluster information with error: {e!s}"
            ) from e
        logger.info("Raft ready to reinitialise")

    def reinitialise_raft_data(self) -> None:
        """Reinitialise the raft journals and promoting the unit to leader. Should only be run on sync replicas."""
        logger.info("Rerendering patroni config without peers")
        self.charm.update_config(no_peers=True)
        logger.info("Starting patroni")
        self.charm.patroni_manager.start_patroni()

        logger.info("Waiting for new raft cluster to initialise")
        for attempt in Retrying(wait=wait_fixed(5)):
            with attempt:
                health_status = self.charm.patroni_manager.get_patroni_health()
                if (
                    health_status["role"] not in ["leader", "master"]
                    or health_status["state"] != "running"
                ):
                    raise RaftNotPromotedError()

        logger.info("Restarting patroni")
        self.charm.patroni_manager.restart_patroni()
        for attempt in Retrying(wait=wait_fixed(5)):
            with attempt:
                found_postgres = False
                for proc in psutil.process_iter(["name"]):
                    if proc.name() == "postgres":
                        found_postgres = True
                        break
                if not found_postgres:
                    raise RaftPostgresqlNotUpError()
        logger.info("Raft should be unstuck")

    def cleanup_raft_cluster(self) -> bool:
        """Cleanup RAFT members not belonging to the current cluster or not a related watcher."""
        # Get Raft cluster status to find all members
        try:
            if not self.charm.patroni_manager.is_patroni_running():
                logger.warning("Raft cleanup: Patroni service not running.")
                return True
            syncobj_util = TcpUtility(password=self.raft_password, timeout=3)
            if raft_status := syncobj_util.executeCommand(f"127.0.0.1:{RAFT_PORT}", ["status"]):
                # Find all partner nodes in the Raft cluster
                # Keys look like: partner_node_status_server_10.131.50.142:2222
                for key in raft_status:
                    if key.startswith(RAFT_PARTNER_PREFIX) and raft_status[key] != 2:
                        member_addr = key.replace(RAFT_PARTNER_PREFIX, "")
                        member_ip = member_addr.split(":")[0]

                        # Check if this is a stale watcher (not a PostgreSQL node and not current watcher)
                        if (
                            member_ip not in self.charm._units_ips
                            and member_addr != self.charm.watcher_offer.watcher_raft_address
                        ):
                            logger.info(f"Removing stale Raft member: {member_addr}")
                            self.remove_raft_member(member_addr)
                            self.charm._remove_from_members_ips(member_ip)
                return True
            return False
        except Exception as e:
            logger.debug(f"Error during Raft cleanup: {e}")
            return False

    def _set_stuck_raft_flag(self) -> None:
        self.charm.set_unit_status(BlockedStatus("Raft majority loss, run: promote-to-primary"))
        logger.warning("Remove raft member: Stuck raft cluster detected")
        data_flags = {"raft_stuck": "True"}
        self.charm.unit_peer_data.update(data_flags)

        # Leader doesn't always trigger when changing it's own peer data.
        if self.charm.unit.is_leader():
            self.charm.on[PEER_RELATION].relation_changed.emit(
                unit=self.charm.unit,
                app=self.charm.app,
                relation=self.charm.model.get_relation(PEER_RELATION),
            )

    def get_raft_status(self) -> dict | None:
        """Get local raft status."""
        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=self.raft_password, timeout=3)

        raft_host = f"127.0.0.1:{RAFT_PORT}"
        with suppress(UtilityException):
            return syncobj_util.executeCommand(raft_host, ["status"])

    def remove_raft_member(
        self, member_address: str, remote_address: str | None = None, set_raft_flags: bool = True
    ) -> None:
        """Remove a member from the raft cluster.

        The raft cluster is a different cluster from the Patroni cluster.
        It is responsible for defining which Patroni member can update
        the primary member in the DCS.

        Raises:
            RaftMemberNotFoundError: if the member to be removed
                is not part of the raft cluster.
        """
        if self.charm.has_raft_keys():
            logger.debug("Remove raft member: Raft already in recovery")
            return

        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=self.raft_password, timeout=3)

        raft_host = remote_address if remote_address else f"127.0.0.1:{RAFT_PORT}"
        try:
            raft_status = syncobj_util.executeCommand(raft_host, ["status"])
        except UtilityException as e:
            logger.warning("Remove raft member: Cannot connect to raft cluster")
            raise RemoveRaftMemberFailedError() from e
        if not raft_status:
            logger.warning("Remove raft member: No raft status")
            raise RemoveRaftMemberFailedError() from None

        # Check whether the member is still part of the raft cluster.
        if f"{RAFT_PARTNER_PREFIX}{member_address}" not in raft_status:
            logger.debug("Remove raft member: Address already removed")
            return

        # If there's no quorum and the leader left raft cluster is stuck
        if raft_status["has_quorum"] and not raft_status["leader"]:
            logger.warning("Remove raft member: No raft leader")
            raise RemoveRaftMemberFailedError() from None
        if (
            not raft_status["has_quorum"]
            and (not raft_status["leader"] or raft_status["leader"].address == member_address)
            and set_raft_flags
        ):
            self._set_stuck_raft_flag()
            return

        # Remove the member from the raft cluster.
        try:
            result = syncobj_util.executeCommand(raft_host, ["remove", member_address])
        except UtilityException as e:
            logger.debug("Remove raft member: Remove call failed")
            raise RemoveRaftMemberFailedError() from e

        if not result or not result.startswith("SUCCESS"):
            logger.debug(f"Remove raft member: Remove call not successful with {result}")
            raise RemoveRaftMemberFailedError() from None

    def add_raft_member(self, member_address: str, remote_address: str | None = None) -> None:
        """Add a member to the raft cluster."""
        if self.charm.has_raft_keys():
            logger.debug("Add raft member: Raft already in recovery")
            return

        # Get the status of the raft cluster.
        syncobj_util = TcpUtility(password=self.raft_password, timeout=3)

        raft_host = remote_address if remote_address else f"127.0.0.1:{RAFT_PORT}"
        try:
            raft_status = syncobj_util.executeCommand(raft_host, ["status"])
        except UtilityException as e:
            logger.warning("Add raft member: Cannot connect to raft cluster")
            raise AddRaftMemberFailedError() from e

        if not raft_status:
            logger.warning("Add raft member: No raft status")
            raise AddRaftMemberFailedError() from None

        # Check whether the member is still part of the raft cluster.
        if f"{RAFT_PARTNER_PREFIX}{member_address}" in raft_status:
            logger.debug("Add raft member: Address already added")
            return

        try:
            result = syncobj_util.executeCommand(raft_host, ["add", member_address])
        except UtilityException as e:
            logger.debug("Add raft member: Remove call failed")
            raise AddRaftMemberFailedError() from e

        if not result or not result.startswith("SUCCESS"):
            logger.debug(f"Add raft member: Remove call not successful with {result}")
            raise AddRaftMemberFailedError() from None
