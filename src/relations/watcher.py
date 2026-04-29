# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Watcher Relation implementation.

This module handles the relation between the PostgreSQL charm and a watcher/witness charm
that participates in the Raft consensus for stereo mode (2-node PostgreSQL clusters).

The watcher provides quorum without storing data, enabling automatic failover
when one of the two PostgreSQL nodes becomes unavailable.
"""

import contextlib
import json
import logging
import os
from functools import cached_property
from typing import TYPE_CHECKING

from ops import (
    Object,
    Relation,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationJoinedEvent,
    Secret,
    SecretNotFoundError,
)
from pysyncobj.utility import TcpUtility

from constants import (
    RAFT_PARTNER_PREFIX,
    RAFT_PASSWORD_KEY,
    RAFT_PORT,
    REPLICATION_CONSUMER_RELATION,
    REPLICATION_OFFER_RELATION,
    WATCHER_OFFER_RELATION,
    WATCHER_PASSWORD_KEY,
    WATCHER_SECRET_LABEL,
    WATCHER_USER,
)
from utils import new_password

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)


class PostgreSQLWatcherRelation(Object):
    """Handles the watcher relation for stereo mode support."""

    def __init__(self, charm: "PostgresqlOperatorCharm"):
        """Initialize the watcher relation handler.

        Args:
            charm: The PostgreSQL operator charm instance.
        """
        super().__init__(charm, WATCHER_OFFER_RELATION)
        self.charm = charm

        self.framework.observe(
            self.charm.on[WATCHER_OFFER_RELATION].relation_joined,
            self._on_watcher_relation_joined,
        )
        self.framework.observe(
            self.charm.on[WATCHER_OFFER_RELATION].relation_changed,
            self._on_watcher_relation_changed,
        )
        self.framework.observe(
            self.charm.on[WATCHER_OFFER_RELATION].relation_broken,
            self._on_watcher_relation_broken,
        )

    @cached_property
    def _relation(self) -> Relation | None:
        """Return the watcher relation if it exists."""
        return self.model.get_relation(WATCHER_OFFER_RELATION)

    @property
    def is_watcher_connected(self) -> bool:
        """Check if a watcher is connected to this cluster.

        Returns:
            True if a watcher is connected, False otherwise.
        """
        try:
            syncobj_util = TcpUtility(password=self.charm._patroni.raft_password, timeout=3)
            raft_status = syncobj_util.executeCommand(f"127.0.0.1:{RAFT_PORT}", ["status"])
            if raft_status:
                # Check if watcher is in the partner_node_status entries
                member_key = f"{RAFT_PARTNER_PREFIX}{self.watcher_raft_address}"
                return member_key in raft_status
        except Exception as e:
            logger.debug(f"Error checking Raft membership: {e}")
        return False

    def enable_watcher(self) -> None:
        """Clear up disable flag."""
        if not self._relation or not self.charm.unit.is_leader():
            return None

        self._relation.data[self.charm.app].pop("disable-watcher", None)
        self.update_watcher_secret()

    def disable_watcher(self) -> None:
        """Inform watcher to stop service."""
        if not self._relation or not self.charm.unit.is_leader():
            return None

        self._relation.data[self.charm.app].update({"disable-watcher": "True"})
        try:
            self.charm._patroni.remove_raft_member(self.watcher_raft_address)
        except Exception as e:
            logger.warning(f"Error remove Raft watcher: {e}")

    @cached_property
    def is_active(self) -> bool:
        """Check if the watcher should be added to peers."""
        if not self._relation:
            return False

        return self._relation.data[self._relation.app].get("raft-status") == "connected"

    @cached_property
    def watcher_raft_address(self) -> str | None:
        """Return the watcher's Raft address for inclusion in partner_addrs.

        Returns:
            The watcher's Raft address (ip:port), or None if not available.
        """
        if not self._relation:
            return None

        unit_address = None
        port = None
        # Get the watcher unit address from the relation data
        for unit in self._relation.units:
            if unit_address := self._relation.data[unit].get("unit-address"):
                break
        port_str = self._relation.data[self._relation.app].get("watcher-raft-port")
        if port_str:
            try:
                port = int(port_str)
            except ValueError:
                logger.warning(f"Invalid watcher-raft-port value: {port_str}")

        if unit_address and port is not None:
            return f"{unit_address}:{port}"
        return None

    def _on_watcher_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle a new watcher joining the relation.

        Shares cluster information including Raft password and PostgreSQL endpoints
        with the watcher charm.

        Args:
            event: The relation joined event.
        """
        # Every unit should publish its own per-unit data.
        self.update_unit_address(event.relation)

        if not self.charm.unit.is_leader():
            return

        logger.info("Watcher relation joined, sharing cluster information")

        # Ensure watcher user exists before creating the secret,
        # so both raft-password and watcher-password are included from the start
        watcher_pw = self._ensure_watcher_user()

        # Create or get the watcher secret containing Raft password and watcher password
        secret = self._get_or_create_watcher_secret(watcher_password=watcher_pw)
        if secret is None:
            logger.warning("Failed to create watcher secret, deferring event")
            event.defer()
            return

        # Grant the secret to the watcher application
        try:
            secret.grant(event.relation)
        except Exception as e:
            logger.warning(f"Failed to grant secret to watcher: {e}")

        # Update relation data with cluster information
        self._update_relation_data(event.relation)

    def _on_watcher_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle watcher relation data changes.

        Updates Patroni configuration to include the watcher in the Raft cluster.

        Args:
            event: The relation changed event.
        """
        # Keep this unit's relation data current on every relation-changed hook.
        self.update_unit_address(event.relation)

        if not self.charm.is_cluster_initialised:
            logger.debug("Cluster not initialized, deferring watcher relation changed")
            event.defer()
            return

        watcher_address = None
        for unit in event.relation.units:
            if unit_address := event.relation.data[unit].get("unit-address"):
                watcher_address = unit_address
                break

        if watcher_address:
            logger.info(f"Watcher address updated: {watcher_address}")
            # Only the leader handles Raft membership changes and user management
            # to avoid race conditions between multiple PostgreSQL units
            if self.charm.unit.is_leader():
                self._cleanup_old_watcher_from_raft()
                self._ensure_watcher_user()
            # Update Patroni configuration to include watcher in Raft
            self.charm.update_config()

        # Update relation data for the watcher
        if self.charm.unit.is_leader():
            self._update_relation_data(event.relation)

    def _cleanup_old_watcher_from_raft(self) -> None:
        """Remove any old watcher IPs from Raft that differ from the current watcher.

        When a watcher unit is replaced (e.g., destroyed and re-deployed), it gets
        a new IP address. The old IP remains in the Raft cluster membership, which
        prevents the new watcher from being recognized as a valid cluster member.
        This method finds and removes any such stale watcher entries.

        Args:
            current_watcher_address: The current watcher's IP address.
        """
        # Get all PostgreSQL unit IPs (these should stay in the cluster)
        # Use _units_ips for fresh IPs from unit relation data
        pg_ips = set(self.charm._units_ips)
        port_postfix = str(RAFT_PORT)

        # Get Raft cluster status to find all members
        try:
            syncobj_util = TcpUtility(password=self.charm._patroni.raft_password, timeout=3)
            if raft_status := syncobj_util.executeCommand(f"127.0.0.1:{RAFT_PORT}", ["status"]):
                # Find all partner nodes in the Raft cluster
                # Keys look like: partner_node_status_server_10.131.50.142:2222
                stale_members: list[str] = []
                for key in raft_status:
                    if (
                        key.startswith(RAFT_PARTNER_PREFIX)
                        and not key.endswith(port_postfix)
                        and raft_status[key] != 2
                    ):
                        member_addr = key.replace(RAFT_PARTNER_PREFIX, "")
                        member_ip = member_addr.split(":")[0]

                        # Check if this is a stale watcher (not a PostgreSQL node and not current watcher)
                        if member_ip not in pg_ips and member_addr != self.watcher_raft_address:
                            stale_members.append(member_addr)

                # Remove stale watcher members
                for stale_addr in stale_members:
                    logger.info(f"Removing stale watcher from Raft cluster: {stale_addr}")
                    self._remove_watcher_from_raft(stale_addr)
        except Exception as e:
            logger.debug(f"Error during Raft cleanup: {e}")

    def _on_watcher_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle watcher relation being broken.

         Updates Patroni configuration to remove the watcher from the Raft cluster.

        Args:
            event: The relation broken event.
        """
        logger.info("Watcher relation broken, updating Patroni configuration")

        if not self.charm.is_cluster_initialised:
            return

        self._cleanup_old_watcher_from_raft()
        # Update Patroni configuration without the watcher
        self.charm.update_config()

    def _remove_watcher_from_raft(self, watcher_address: str) -> None:
        """Remove the watcher from the Raft cluster.

        This is critical for maintaining correct quorum calculations. If a dead
        watcher remains in the cluster membership, it counts toward the total
        node count, making it harder to achieve quorum.

        Args:
            watcher_address: The watcher's IP address.
        """
        if self.watcher_raft_address:
            logger.info(f"Removing watcher from Raft cluster: {watcher_address}")
            self.charm._patroni.remove_raft_member(watcher_address)

        if self.charm.is_cluster_initialised:
            self.charm.update_config()

    def _ensure_watcher_user(self) -> str | None:
        """Ensure the watcher PostgreSQL user exists for health checks.

        Creates the watcher user if it doesn't exist, and updates the watcher
        secret with the password so the watcher charm can authenticate.

        Returns:
            The watcher password, or None if user creation failed.
        """
        if not self.charm.is_cluster_initialised:
            logger.debug("Cluster not initialized, cannot create watcher user")
            return None

        try:
            users = self.charm.postgresql.list_users()
            if WATCHER_USER in users:
                logger.debug(f"User {WATCHER_USER} already exists")
                # Get existing password from secret if available
                try:
                    secret = self.charm.model.get_secret(label=WATCHER_SECRET_LABEL)
                    content = secret.get_content(refresh=True)
                    existing_pw = content.get(WATCHER_PASSWORD_KEY)
                    if existing_pw:
                        return existing_pw
                    # Password not in secret — fall through to regenerate
                except SecretNotFoundError:
                    # Secret doesn't exist yet, will be created below with new password
                    pass

            # Generate a password for the watcher user
            watcher_password = new_password()

            # Create the watcher user (minimal privileges - only needs to connect and run SELECT 1)
            if WATCHER_USER not in users:
                logger.info(f"Creating PostgreSQL user: {WATCHER_USER}")
                self.charm.postgresql.create_user(WATCHER_USER, watcher_password)
            else:
                # User exists but we don't have the password, update it
                logger.info(f"Updating password for PostgreSQL user: {WATCHER_USER}")
                self.charm.postgresql.update_user_password(WATCHER_USER, watcher_password)

            # Grant connect privilege on postgres database (for health checks)
            self.charm.postgresql.grant_database_privileges_to_user(
                WATCHER_USER, "postgres", ["connect"]
            )

            # Update the secret to include the watcher password
            self._update_watcher_secret_with_password(watcher_password)

            return watcher_password

        except Exception as e:
            logger.error(f"Failed to ensure watcher user: {e}")
            return None

    def _update_watcher_secret_with_password(self, watcher_password: str) -> None:
        """Update the watcher secret to include the watcher password.

        Args:
            watcher_password: The password for the watcher PostgreSQL user.
        """
        try:
            secret = self.charm.model.get_secret(label=WATCHER_SECRET_LABEL)
            content = secret.get_content(refresh=True)
            content[WATCHER_PASSWORD_KEY] = watcher_password
            secret.set_content(content)
            logger.info("Updated watcher secret with watcher password")
        except SecretNotFoundError:
            logger.warning(
                "Watcher secret not found, password change cannot be propagated to watcher. "
                "It will be synced on next relation-changed event."
            )
        except Exception as e:
            logger.error(f"Failed to update watcher secret with password: {e}")

    def _get_existing_watcher_password(self) -> str | None:
        """Get the watcher password from an existing secret if available."""
        try:
            secret = self.charm.model.get_secret(label=WATCHER_SECRET_LABEL)
            content = secret.get_content(refresh=True)
            return content.get(WATCHER_PASSWORD_KEY)
        except SecretNotFoundError:
            return None
        except Exception as e:
            logger.debug(f"Failed to get existing watcher password: {e}")
            return None

    def _get_or_create_watcher_secret(self, watcher_password: str | None = None) -> Secret | None:
        """Get or create the secret for sharing Raft credentials with the watcher.

        Args:
            watcher_password: Optional watcher password to include in the secret.

        Returns:
            The Juju secret containing Raft password, or None if creation failed.
        """
        try:
            secret = self.charm.model.get_secret(label=WATCHER_SECRET_LABEL)
            logger.debug("Found existing watcher secret")
            return secret
        except SecretNotFoundError:
            logger.debug("No existing watcher secret found, creating new one")

        # Get the Raft password from the internal secret
        try:
            raft_password = self.charm._patroni.raft_password
        except Exception as e:
            logger.warning(f"Error getting raft_password: {e}")
            raft_password = None

        if not raft_password:
            logger.warning("Raft password not available, cannot create secret")
            return None

        # Create a new secret with the Raft password (and watcher password if available)
        try:
            content = {RAFT_PASSWORD_KEY: raft_password}
            # Include watcher password if provided, or look it up from existing secret
            watcher_pw = watcher_password or self._get_existing_watcher_password()
            if watcher_pw:
                content[WATCHER_PASSWORD_KEY] = watcher_pw
            secret = self.charm.model.app.add_secret(
                content=content,
                label=WATCHER_SECRET_LABEL,
            )
            logger.info("Created watcher secret")
            return secret
        except Exception as e:
            logger.error(f"Failed to create watcher secret: {e}")
            return None

    def _update_relation_data(self, relation: Relation) -> None:
        """Update the relation data with cluster information.

        Args:
            relation: The watcher relation.
        """
        if not self.charm.unit.is_leader():
            return

        # Get the secret ID for sharing
        try:
            secret = self.charm.model.get_secret(label=WATCHER_SECRET_LABEL)
            secret_id = secret.id
            if not secret_id:
                # When a secret is retrieved by label, the ops library may lazily load the ID.
                # Calling get_info() forces it to resolve.
                secret_id = secret.get_info().id
            if secret_id is None:
                logger.warning("Watcher secret has no ID")
                return
            # Ensure the secret is granted to the watcher relation (handles
            # cases where the secret was recreated after initial relation_joined)
            with contextlib.suppress(Exception):
                secret.grant(relation)
        except SecretNotFoundError:
            logger.warning("Watcher secret not found")
            return
        except Exception as e:
            logger.error(f"Error getting secret: {e}")
            return

        # Collect PostgreSQL unit endpoints using fresh IPs from unit relation data.
        # _units_ips reads directly from unit relation data (always fresh), while
        # _peer_members_ips reads from app peer data (may be stale after network disruptions).
        pg_endpoints: list[str] = sorted(self.charm._units_ips)
        if not pg_endpoints:
            logger.warning("No PostgreSQL endpoints available")
            return

        # Update relation data
        relation.data[self.charm.app].update({
            "cluster-name": self.charm.cluster_name,
            "raft-secret-id": secret_id,
            "version": self.charm._patroni.get_postgresql_version(),
            "raft-partner-addrs": json.dumps(pg_endpoints),
            "raft-port": str(RAFT_PORT),
            "patroni-cas": self.charm.tls.get_peer_ca_bundle(),
            "standby-clusters": json.dumps(self._get_standby_clusters()),
            "tls-enabled": "true" if self.charm.is_tls_enabled else "false",
        })
        self.update_watcher_secret()

        # Also share this unit's per-unit data.
        self.update_unit_address(relation)

    def update_unit_address(self, relation: Relation | None = None) -> None:
        """Update this unit's address in the watcher relation.

        Called when the unit's IP changes (e.g., after network isolation).
        This updates unit-specific data in the relation, not application data.
        Can be called by any unit, not just the leader.
        """
        if relation is None:
            relation = self._relation

        if not relation:
            return

        unit_ip = self.charm._unit_ip
        if unit_ip is None:
            return

        changed = False
        current_address = relation.data[self.charm.unit].get("unit-address")
        if current_address != unit_ip:
            logger.info(
                f"Updating unit-address in watcher relation from {current_address} to {unit_ip}"
            )
            relation.data[self.charm.unit]["unit-address"] = unit_ip
            changed = True

        unit_az = os.environ.get("JUJU_AVAILABILITY_ZONE")
        current_az = relation.data[self.charm.unit].get("unit-az")
        if unit_az and current_az != unit_az:
            relation.data[self.charm.unit]["unit-az"] = unit_az
            changed = True

        if changed:
            logger.debug("Updated watcher relation unit data")

    def update_endpoints(self) -> None:
        """Update the watcher with current cluster endpoints.

        Called when cluster membership changes (peer joins/departs).
        Also dynamically adds new PostgreSQL peers to the running Raft cluster.
        """
        if self.charm.unit.is_leader() and (relation := self._relation):
            self._update_relation_data(relation)

    def _get_standby_clusters(self) -> list[str]:
        """Return the names of related standby clusters."""
        standby_clusters = []
        for relation in [
            self.model.get_relation(REPLICATION_OFFER_RELATION),
            self.model.get_relation(REPLICATION_CONSUMER_RELATION),
        ]:
            if relation is None:
                continue
            # We are interested in the other side's application name
            if relation.app and self.charm.async_replication.is_primary_cluster():
                standby_clusters.append(relation.app.name)
        return sorted(set(standby_clusters))

    def update_watcher_secret(self) -> None:
        """Update the watcher secret with current Raft password.

        Called when credentials are rotated. Preserves existing secret content
        (e.g., watcher-password) while updating the Raft password.
        """
        if not self.charm.unit.is_leader():
            return

        try:
            if raft_password := self.charm._patroni.raft_password:
                secret = self.charm.model.get_secret(label=WATCHER_SECRET_LABEL)
                content = secret.get_content(refresh=True)
                if content.get(RAFT_PASSWORD_KEY) != raft_password:
                    content[RAFT_PASSWORD_KEY] = raft_password
                    secret.set_content(content)
                    logger.info("Updated watcher secret with new Raft password")
        except SecretNotFoundError:
            logger.debug("Watcher secret not found, nothing to update")

    def ensure_watcher_in_raft(self) -> None:
        """Ensure the connected watcher is in the Raft cluster and has fresh endpoint data.

        Called periodically from update_status to handle cases where Juju
        relation events weren't delivered (e.g., when a watcher unit is replaced).
        This method:
        1. Cleans up any stale watcher IPs from the Raft cluster
        2. Adds the current watcher to Raft if not present
        3. Updates the watcher relation data with fresh PostgreSQL IPs

        The last point is critical because after network disruptions that cause IP
        changes, the watcher may have stale pg-endpoints and be unable to health
        check the PostgreSQL nodes properly.
        """
        if not self.charm.is_cluster_initialised or not self.is_active:
            return

        # Only the leader handles Raft membership changes to avoid races
        if self.charm.unit.is_leader():
            self._cleanup_old_watcher_from_raft()

            # Update watcher relation data with fresh PostgreSQL IPs
            if relation := self._relation:
                self._update_relation_data(relation)
