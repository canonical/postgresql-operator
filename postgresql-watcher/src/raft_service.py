#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Standalone pysyncobj Raft service for the PostgreSQL watcher.

This script runs a minimal pysyncobj node that participates in Raft consensus
without needing the charmed-postgresql snap. It's designed to be run as a
systemd service managed by the watcher charm.

The watcher implements a KVStoreTTL-compatible class so it can participate in
the same Raft cluster as Patroni's DCS. The watcher doesn't actually use the
replicated data - it only provides a vote for quorum in 2-node clusters.

Usage:
    python3 raft_service.py --self-addr IP:PORT --partners IP1:PORT,IP2:PORT --password PASSWORD
"""

import argparse
import logging
import os
import signal
import sys
import time
from typing import Any, Callable, Dict, Optional, Union

from pysyncobj import SyncObj, SyncObjConf, replicated

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WatcherKVStoreTTL(SyncObj):
    """A pysyncobj node compatible with Patroni's KVStoreTTL.

    This class implements the same @replicated methods as Patroni's KVStoreTTL
    so that it can participate in the same Raft cluster. The watcher doesn't
    actually store or use the data - it only provides a vote for quorum.

    The methods must have the same signatures as Patroni's KVStoreTTL for
    the Raft log entries to be applied correctly.

    IMPORTANT: This class also implements _onTick with __expire_keys logic,
    which is critical for failover. When the watcher becomes the Raft leader
    (e.g., when the PostgreSQL primary is network-isolated), it must expire
    stale leader keys so that a replica can acquire leadership.
    """

    def __init__(self, self_addr: str, partner_addrs: list[str], password: str, data_dir: str = ""):
        """Initialize the Raft node.

        Args:
            self_addr: This node's address (host:port).
            partner_addrs: List of partner addresses.
            password: Raft cluster password.
            data_dir: Directory for Raft state files.
        """
        file_template = ""
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            file_template = os.path.join(data_dir, self_addr.replace(":", "_"))

        conf = SyncObjConf(
            password=password,
            autoTick=True,
            dynamicMembershipChange=True,
            fullDumpFile=f"{file_template}.dump" if file_template else None,
            journalFile=f"{file_template}.journal" if file_template else None,
        )
        super().__init__(self_addr, partner_addrs, conf=conf)
        # Storage for replicated data - needed for TTL expiry logic
        self.__data: Dict[str, Dict[str, Any]] = {}
        # Track keys being expired to avoid duplicate expiration calls
        self.__limb: Dict[str, bool] = {}
        logger.info(f"WatcherKVStoreTTL initialized: self={self_addr}, partners={partner_addrs}")

    @replicated
    def _set(self, key: str, value: Dict[str, Any], **kwargs: Any) -> Union[bool, Dict[str, Any]]:
        """Replicated set operation - compatible with Patroni's KVStoreTTL._set.

        The watcher doesn't actually use this data, but must implement the method
        to be compatible with the Raft cluster.
        """
        value['index'] = self.raftLastApplied + 1
        self.__data[key] = value
        return value

    @replicated
    def _delete(self, key: str, recursive: bool = False, **kwargs: Any) -> bool:
        """Replicated delete operation - compatible with Patroni's KVStoreTTL._delete.

        The watcher doesn't actually use this data, but must implement the method
        to be compatible with the Raft cluster.
        """
        if recursive:
            for k in list(self.__data.keys()):
                if k.startswith(key):
                    self.__data.pop(k, None)
        else:
            self.__data.pop(key, None)
        return True

    @replicated
    def _expire(self, key: str, value: Dict[str, Any], callback: Optional[Callable[..., Any]] = None) -> None:
        """Replicated expire operation - compatible with Patroni's KVStoreTTL._expire.

        The watcher doesn't actually use this data, but must implement the method
        to be compatible with the Raft cluster.
        """
        self.__data.pop(key, None)

    def __expire_keys(self) -> None:
        """Expire keys that have exceeded their TTL.

        This method is called by _onTick when this node is the Raft leader.
        It checks all stored keys for expired TTL values and triggers the
        replicated _expire operation for them.

        This is critical for failover: when the PostgreSQL primary is isolated,
        its leader key TTL will expire, and this method ensures that expiry
        is processed so a replica can acquire leadership.
        """
        current_time = time.time()
        for key, value in list(self.__data.items()):
            if 'expire' in value and value['expire'] <= current_time:
                # Check if we're already processing this key's expiration
                if key not in self.__limb:
                    self.__limb[key] = True
                    logger.info(f"Expiring key {key} (TTL expired)")
                    # Call the replicated _expire method to remove the key
                    # across all nodes in the Raft cluster
                    self._expire(key, value)

    def _onTick(self, timeToWait: float = 0.0) -> None:
        """Called periodically by pysyncobj's auto-tick mechanism.

        When this node is the Raft leader, it runs __expire_keys to check
        for and remove expired TTL entries. This is essential for Patroni
        failover to work correctly.

        Args:
            timeToWait: Time to wait before next tick (passed to parent).
        """
        # Call parent's _onTick first
        super()._onTick(timeToWait)

        # If we're the leader, expire any keys that have exceeded their TTL
        if self._isLeader():
            self.__expire_keys()
        else:
            # Clear limb tracking when not leader
            self.__limb.clear()


class WatcherRaftNode:
    """A wrapper around WatcherKVStoreTTL for the watcher charm.

    This node participates in Raft consensus without storing any
    application data - it only provides a vote for quorum.
    """

    def __init__(self, self_addr: str, partner_addrs: list[str], password: str, data_dir: str = ""):
        """Initialize the Raft node.

        Args:
            self_addr: This node's address (host:port).
            partner_addrs: List of partner addresses.
            password: Raft cluster password.
            data_dir: Directory for Raft state files.
        """
        self._node = WatcherKVStoreTTL(self_addr, partner_addrs, password, data_dir)
        logger.info(f"WatcherRaftNode initialized: self={self_addr}, partners={partner_addrs}")

    def get_status(self) -> dict:
        """Get the Raft node status."""
        return self._node.getStatus()

    def destroy(self) -> None:
        """Clean up the Raft node."""
        self._node.destroy()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="PostgreSQL Watcher Raft Service"
    )
    parser.add_argument(
        "--self-addr",
        required=True,
        help="This node's address (IP:PORT)"
    )
    parser.add_argument(
        "--partners",
        required=True,
        help="Comma-separated list of partner addresses (IP1:PORT,IP2:PORT)"
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Raft cluster password"
    )
    parser.add_argument(
        "--data-dir",
        default="/var/lib/watcher-raft",
        help="Directory for Raft state files"
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    partner_addrs = [addr.strip() for addr in args.partners.split(",") if addr.strip()]

    logger.info(f"Starting Watcher Raft node: {args.self_addr}")
    logger.info(f"Partners: {partner_addrs}")

    node: Optional[WatcherRaftNode] = None
    shutdown_requested = False

    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        logger.info(f"Received signal {signum}, shutting down...")
        shutdown_requested = True

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        node = WatcherRaftNode(
            self_addr=args.self_addr,
            partner_addrs=partner_addrs,
            password=args.password,
            data_dir=args.data_dir,
        )

        logger.info("Raft node started, entering main loop")

        # Main loop - just keep running until signaled
        while not shutdown_requested:
            time.sleep(1)
            # Periodically log status
            try:
                status = node.get_status()
                has_quorum = status.get("has_quorum", False)
                leader = status.get("leader")
                if has_quorum:
                    logger.debug(f"Raft status: quorum=True, leader={leader}")
                else:
                    logger.warning(f"Raft status: quorum=False, leader={leader}")
            except Exception as e:
                logger.debug(f"Failed to get status: {e}")

    except Exception as e:
        logger.error(f"Error running Raft node: {e}")
        return 1
    finally:
        if node:
            logger.info("Destroying Raft node...")
            node.destroy()

    logger.info("Raft service stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
