# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Async Replication implementation.

The highest "promoted-cluster-counter" value is used to determine the primary cluster.
The application in any side of the relation which has the highest value in its application
relation databag is considered the primary cluster.

The "unit-promoted-cluster-counter" field in the unit relation databag is used to determine
if the unit is following the promoted cluster. If the value is the same as the highest value
in the application relation databag, then the unit is following the promoted cluster.
Otherwise, it's needed to restart the database in the unit to follow the promoted cluster
if the unit is from the standby cluster (the one that was not promoted).
"""

import contextlib
import json
import logging
import os
import pwd
import shutil
import subprocess
import typing
from datetime import datetime
from pathlib import Path
from subprocess import run

from ops import (
    ActionEvent,
    ActiveStatus,
    Application,
    BlockedStatus,
    MaintenanceStatus,
    Object,
    Relation,
    RelationChangedEvent,
    RelationDepartedEvent,
    Secret,
    SecretChangedEvent,
    SecretNotFoundError,
    WaitingStatus,
)
from tenacity import RetryError, Retrying, stop_after_attempt, stop_after_delay, wait_fixed

from cluster import ClusterNotPromotedError, NotReadyError, StandbyClusterAlreadyPromotedError
from constants import (
    APP_SCOPE,
    PATRONI_CONF_PATH,
    PEER,
    POSTGRESQL_DATA_PATH,
    REPLICATION_CONSUMER_RELATION,
    REPLICATION_OFFER_RELATION,
)

logger = logging.getLogger(__name__)


READ_ONLY_MODE_BLOCKING_MESSAGE = "Standalone read-only cluster"
# Labels are not confidential
SECRET_LABEL = "async-replication-secret"  # noqa: S105

if typing.TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


class AsyncReplicationError(Exception):
    """Exception class for Async replication."""


class PostgreSQLAsyncReplication(Object):
    """Defines the async-replication management logic."""

    def __init__(self, charm: "PostgresqlOperatorCharm"):
        """Constructor."""
        super().__init__(charm, "postgresql")
        self.charm = charm
        self.framework.observe(
            self.charm.on[REPLICATION_OFFER_RELATION].relation_joined,
            self._on_async_relation_joined,
        )
        self.framework.observe(
            self.charm.on[REPLICATION_CONSUMER_RELATION].relation_joined,
            self._on_async_relation_joined,
        )
        self.framework.observe(
            self.charm.on[REPLICATION_OFFER_RELATION].relation_changed,
            self._on_async_relation_changed,
        )
        self.framework.observe(
            self.charm.on[REPLICATION_CONSUMER_RELATION].relation_changed,
            self._on_async_relation_changed,
        )

        # Departure events
        self.framework.observe(
            self.charm.on[REPLICATION_OFFER_RELATION].relation_departed,
            self._on_async_relation_departed,
        )
        self.framework.observe(
            self.charm.on[REPLICATION_CONSUMER_RELATION].relation_departed,
            self._on_async_relation_departed,
        )
        self.framework.observe(
            self.charm.on[REPLICATION_OFFER_RELATION].relation_broken,
            self._on_async_relation_broken,
        )
        self.framework.observe(
            self.charm.on[REPLICATION_CONSUMER_RELATION].relation_broken,
            self._on_async_relation_broken,
        )

        # Actions
        self.framework.observe(
            self.charm.on.create_replication_action, self._on_create_replication
        )

        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)

    @property
    def _unit_ip(self) -> str:
        """Return this unit IP address for the replication relation."""
        if not self._relation:
            raise AsyncReplicationError("No relation to get IP for")

        if self._relation.name == REPLICATION_OFFER_RELATION:
            ip = self.charm._replication_offer_ip
        else:
            ip = self.charm._replication_consumer_ip

        if not ip:
            raise AsyncReplicationError(f"No IP set for {self._relation.name}")
        return ip

    def _can_promote_cluster(self, event: ActionEvent) -> bool:
        """Check if the cluster can be promoted."""
        if not self.charm.is_cluster_initialised:
            event.fail("Cluster not initialised yet.")
            return False

        # Check if there is a relation. If not, see if there is a standby leader. If so promote it to leader. If not,
        # fail the action telling that there is no relation and no standby leader.
        relation = self._relation
        if relation is None:
            standby_leader = self.charm._patroni.get_standby_leader()
            if standby_leader is not None:
                try:
                    self.charm._patroni.promote_standby_cluster()
                    if self.charm.app.status.message == READ_ONLY_MODE_BLOCKING_MESSAGE:
                        self.charm.app_peer_data.update({"promoted-cluster-counter": ""})
                        self.set_app_status()
                        self.charm._set_primary_status_message()
                except (StandbyClusterAlreadyPromotedError, ClusterNotPromotedError) as e:
                    event.fail(str(e))
                return False
            event.fail("No relation and no standby leader found.")
            return False

        # Check if this cluster is already the primary cluster. If so, fail the action telling that it's already
        # the primary cluster.
        primary_cluster = self._get_primary_cluster()
        if self.charm.app == primary_cluster:
            event.fail("This cluster is already the primary cluster.")
            return False

        return self._handle_forceful_promotion(event)

    def _configure_primary_cluster(
        self, primary_cluster: Application, event: RelationChangedEvent
    ) -> bool:
        """Configure the primary cluster."""
        if self.charm.app == primary_cluster:
            self.charm.update_config()
            if self.is_primary_cluster() and self.charm.unit.is_leader():
                self._update_primary_cluster_data()
                # If this is a standby cluster, remove the information from DCS to make it
                # a normal cluster.
                if self.charm._patroni.get_standby_leader() is not None:
                    self.charm._patroni.promote_standby_cluster()
                    try:
                        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                            with attempt:
                                if not self.charm.is_primary:
                                    raise ClusterNotPromotedError()
                    except RetryError:
                        logger.debug(
                            "Deferring on_async_relation_changed: standby cluster not promoted yet."
                        )
                        event.defer()
                        return True
            self.charm.unit_peer_data.update({
                "unit-promoted-cluster-counter": self._get_highest_promoted_cluster_counter_value()
            })
            self.charm._set_primary_status_message()
            return True
        return False

    def _configure_standby_cluster(self, event: RelationChangedEvent) -> bool:
        """Configure the standby cluster."""
        if not (relation := self._relation):
            raise AsyncReplicationError("No relation in configure standby cluster")

        if relation.name == REPLICATION_CONSUMER_RELATION and not self._update_internal_secret():
            logger.debug("Secret not found, deferring event")
            event.defer()
            return False
        system_identifier, error = self.get_system_identifier()
        if error is not None:
            raise Exception(error)
        if system_identifier != relation.data[relation.app].get("system-id"):
            # Store current data in a tar.gz file.
            logger.info("Creating backup of data folder")
            filename = f"{POSTGRESQL_DATA_PATH}-{str(datetime.now()).replace(' ', '-').replace(':', '-')}.tar.gz"
            # Input is hardcoded
            subprocess.check_call(f"tar -zcf {filename} {POSTGRESQL_DATA_PATH}".split())  # noqa: S603
            logger.warning("Please review the backup file %s and handle its removal", filename)
        self.charm.app_peer_data["suppress-oversee-users"] = "true"
        return True

    def get_all_primary_cluster_endpoints(self) -> list[str]:
        """Return all the primary cluster endpoints from the standby cluster."""
        if not (relation := self._relation):
            raise AsyncReplicationError("No relation in get all primary endpoints")

        primary_cluster = self._get_primary_cluster()
        # List the primary endpoints only for the standby cluster.
        if relation is None or primary_cluster is None or self.charm.app == primary_cluster:
            return []
        return [
            relation.data[unit]["unit-address"]
            for relation in [
                self.model.get_relation(REPLICATION_OFFER_RELATION),
                self.model.get_relation(REPLICATION_CONSUMER_RELATION),
            ]
            if relation is not None
            for unit in relation.units
            if relation.data[unit].get("unit-address") is not None
        ]

    def _get_highest_promoted_cluster_counter_value(self) -> str:
        """Return the highest promoted cluster counter."""
        promoted_cluster_counter = "0"
        for async_relation in [
            self.model.get_relation(REPLICATION_OFFER_RELATION),
            self.model.get_relation(REPLICATION_CONSUMER_RELATION),
        ]:
            if async_relation is None:
                continue
            for databag in [
                async_relation.data[async_relation.app],
                self.charm.app_peer_data,
            ]:
                relation_promoted_cluster_counter = databag.get("promoted-cluster-counter", "0")
                if int(relation_promoted_cluster_counter) > int(promoted_cluster_counter):
                    promoted_cluster_counter = relation_promoted_cluster_counter
        return promoted_cluster_counter

    def get_partner_addresses(self) -> list[str]:
        """Return the partner addresses."""
        primary_cluster = self._get_primary_cluster()
        if (
            primary_cluster is None
            or self.charm.app == primary_cluster
            or not self.charm.unit.is_leader()
            or self.charm.unit_peer_data.get("unit-promoted-cluster-counter")
            == self._get_highest_promoted_cluster_counter_value()
        ) and (peer_members := self.charm._peer_members_ips):
            logger.debug(f"Partner addresses: {peer_members}")
            return list(peer_members)

        logger.debug("Partner addresses: []")
        return []

    def _get_primary_cluster(self) -> Application | None:
        """Return the primary cluster."""
        primary_cluster = None
        promoted_cluster_counter = "0"
        for async_relation in [
            self.model.get_relation(REPLICATION_OFFER_RELATION),
            self.model.get_relation(REPLICATION_CONSUMER_RELATION),
        ]:
            if async_relation is None:
                continue
            for app, relation_data in {
                async_relation.app: async_relation.data,
                self.charm.app: self.charm.all_peer_data,
            }.items():
                databag = relation_data[app]
                relation_promoted_cluster_counter = databag.get("promoted-cluster-counter", "0")
                if relation_promoted_cluster_counter > promoted_cluster_counter:
                    promoted_cluster_counter = relation_promoted_cluster_counter
                    primary_cluster = app
        return primary_cluster

    def get_primary_cluster_endpoint(self) -> str | None:
        """Return the primary cluster endpoint."""
        primary_cluster = self._get_primary_cluster()
        if primary_cluster is None or self.charm.app == primary_cluster:
            return None
        relation = self._relation
        primary_cluster_data = relation.data[relation.app].get("primary-cluster-data")  # type: ignore
        if primary_cluster_data is None:
            return None
        return json.loads(primary_cluster_data).get("endpoint")

    def _get_secret(self) -> Secret | None:
        """Return async replication necessary secrets."""
        app_secret = self.charm.model.get_secret(label=f"{PEER}.{self.model.app.name}.app")
        content = app_secret.peek_content()

        # Filter out unnecessary secrets.
        shared_content = dict(filter(lambda x: "password" in x[0], content.items()))

        try:
            # Avoid recreating the secret.
            secret = self.charm.model.get_secret(label=SECRET_LABEL)
            if not secret.id:
                # Workaround for the secret id not being set with model uuid.
                secret._id = f"secret://{self.model.uuid}/{secret.get_info().id.split(':')[1]}"
            if secret.peek_content() != shared_content:
                logger.info("Updating outdated secret content")
                secret.set_content(shared_content)
            return secret
        except SecretNotFoundError:
            logger.debug("Secret not found, creating a new one")
            pass

        if self.charm.unit.is_leader():
            return self.charm.model.app.add_secret(content=shared_content, label=SECRET_LABEL)

    def get_standby_endpoints(self) -> list[str]:
        """Return the standby endpoints."""
        if not (relation := self._relation):
            return []

        primary_cluster = self._get_primary_cluster()
        # List the standby endpoints only for the primary cluster.
        if relation is None or primary_cluster is None or self.charm.app != primary_cluster:
            return []
        return [
            relation.data[unit]["unit-address"]
            for relation in [
                self.model.get_relation(REPLICATION_OFFER_RELATION),
                self.model.get_relation(REPLICATION_CONSUMER_RELATION),
            ]
            if relation is not None
            for unit in relation.units
            if relation.data[unit].get("unit-address") is not None
        ]

    def get_system_identifier(self) -> tuple[str | None, str | None]:
        """Returns the PostgreSQL system identifier from this instance."""

        def demote():
            pw_record = pwd.getpwnam("_daemon_")

            def result():
                os.setgid(pw_record.pw_gid)
                os.setuid(pw_record.pw_uid)

            return result

        # Input is hardcoded
        process = run(  # noqa: S603
            [
                f"/snap/charmed-postgresql/current/usr/lib/postgresql/{self.charm._patroni.get_postgresql_version().split('.')[0]}/bin/pg_controldata",
                POSTGRESQL_DATA_PATH,
            ],
            capture_output=True,
            preexec_fn=demote(),
        )
        if process.returncode != 0:
            return None, process.stderr.decode()
        system_identifier = next(
            line
            for line in process.stdout.decode().splitlines()
            if "Database system identifier" in line
        ).split(" ")[-1]
        return system_identifier, None

    def _handle_database_start(self, event: RelationChangedEvent) -> None:
        """Handle the database start in the standby cluster."""
        try:
            if self.charm._patroni.member_started:
                # If the database is started, update the databag in a way the unit is marked as configured
                # for async replication.
                self.charm.unit_peer_data.update({"stopped": ""})
                self.charm.unit_peer_data.update({
                    "unit-promoted-cluster-counter": self._get_highest_promoted_cluster_counter_value()
                })

                if self.charm.unit.is_leader():
                    # If this unit is the leader, check if all units are ready before making the cluster
                    # active again (including the health checks from the update status hook).
                    self.charm.update_config()
                    if all(
                        self.charm.unit_peer_data.get("unit-promoted-cluster-counter")
                        == self._get_highest_promoted_cluster_counter_value()
                        for unit in {*self.charm._peers.units, self.charm.unit}  # type: ignore
                    ):
                        self.charm.app_peer_data.update({"cluster_initialised": "True"})
                    elif self._is_following_promoted_cluster():
                        self.charm.set_unit_status(
                            WaitingStatus("Waiting for the database to be started in all units")
                        )
                        event.defer()
                        return

                self.charm._set_primary_status_message()
            elif not self.charm.unit.is_leader():
                with contextlib.suppress(RetryError):
                    self.charm._patroni.reload_patroni_configuration()
                raise NotReadyError()
            else:
                self.charm.set_unit_status(
                    WaitingStatus("Still starting the database in the standby leader")
                )
                event.defer()
        except NotReadyError:
            self.charm.set_unit_status(WaitingStatus("Waiting for the database to start"))
            logger.debug("Deferring on_async_relation_changed: database hasn't started yet.")
            event.defer()

    def _handle_forceful_promotion(self, event: ActionEvent) -> bool:
        if not event.params.get("force"):
            all_primary_cluster_endpoints = self.get_all_primary_cluster_endpoints()
            if len(all_primary_cluster_endpoints) > 0:
                primary_cluster_reachable = False
                try:
                    primary = self.charm._patroni.get_primary(
                        alternative_endpoints=all_primary_cluster_endpoints
                    )
                    if primary is not None:
                        primary_cluster_reachable = True
                except RetryError:
                    pass
                if not primary_cluster_reachable:
                    event.fail(
                        f"{self._relation.app.name} isn't reachable. Pass `force=true` to promote anyway."  # type: ignore
                    )
                    return False
        else:
            logger.warning(
                "Forcing promotion of %s to primary cluster due to `force=true`.",
                self.charm.app.name,
            )
        return True

    def handle_read_only_mode(self) -> None:
        """Handle read-only mode (standby cluster that lost the relation with the primary cluster)."""
        if not self.charm.is_blocked:
            self.charm._set_primary_status_message()

        if self.charm.unit.is_leader():
            self.set_app_status()

    def _handle_replication_change(self, event: ActionEvent) -> bool:
        if not self._can_promote_cluster(event):
            return False

        relation = self._relation

        # Check if all units from the other cluster published their IPs in the relation data.
        # If not, fail the action telling that all units must publish their pod addresses in the
        # relation data.
        for unit in relation.units:  # type: ignore
            if "unit-address" not in relation.data[unit]:  # type: ignore
                event.fail(
                    "All units from the other cluster must publish their unit addresses in the relation data."
                )
                return False

        system_identifier, error = self.get_system_identifier()
        if error is not None:
            logger.exception(error)
            event.fail("Failed to get system identifier")
            return False

        # Increment the current cluster counter in this application side based on the highest counter value.
        promoted_cluster_counter = int(self._get_highest_promoted_cluster_counter_value())
        promoted_cluster_counter += 1
        logger.debug("Promoted cluster counter: %s", promoted_cluster_counter)

        self._update_primary_cluster_data(promoted_cluster_counter, system_identifier)

        # Emit an async replication changed event for this unit (to promote this cluster before demoting the
        # other if this one is a standby cluster, which is needed to correctly set up the async replication
        # when performing a switchover).
        self._re_emit_async_relation_changed_event()

        return True

    def _is_following_promoted_cluster(self) -> bool:
        """Return True if this unit is following the promoted cluster."""
        if self._get_primary_cluster() is None:
            return False
        return (
            self.charm.unit_peer_data.get("unit-promoted-cluster-counter")
            == self._get_highest_promoted_cluster_counter_value()
        )

    def is_primary_cluster(self) -> bool:
        """Return the primary cluster name."""
        return self.charm.app == self._get_primary_cluster()

    def _on_async_relation_broken(self, _) -> None:
        if not self.charm._peers or self.charm.is_unit_departing:
            logger.debug("Early exit on_async_relation_broken: Skipping departing unit.")
            return

        self.charm.unit_peer_data.update({
            "stopped": "",
            "unit-promoted-cluster-counter": "",
        })

        # If this is the standby cluster, set 0 in the "promoted-cluster-counter" field to set
        # the cluster in read-only mode message also in the other units.
        if self.charm._patroni.get_standby_leader() is not None:
            if self.charm.unit.is_leader():
                self.charm.app_peer_data.update({"promoted-cluster-counter": "0"})
                self.set_app_status()
        else:
            if self.charm.unit.is_leader():
                self.charm.app_peer_data.update({"promoted-cluster-counter": ""})
            self.charm.update_config()

    def _on_async_relation_changed(self, event: RelationChangedEvent) -> None:
        """Update the Patroni configuration if one of the clusters was already promoted."""
        if self.charm.unit.is_leader():
            self.set_app_status()

        primary_cluster = self._get_primary_cluster()
        logger.debug("Primary cluster: %s", primary_cluster)
        if primary_cluster is None:
            logger.debug("Early exit on_async_relation_changed: No primary cluster found.")
            return

        if self._configure_primary_cluster(primary_cluster, event):
            return

        # Return if this is a new unit.
        if not self.charm.unit.is_leader() and self._is_following_promoted_cluster():
            logger.debug("Early exit on_async_relation_changed: following promoted cluster.")
            return

        if not self._stop_database(event):
            return

        if not (self.charm.is_unit_stopped or self._is_following_promoted_cluster()) or not all(
            "stopped" in self.charm.all_peer_data[unit]
            or self.charm.all_peer_data[unit].get("unit-promoted-cluster-counter")
            == self._get_highest_promoted_cluster_counter_value()
            for unit in self.charm._peers.units  # type: ignore
        ):
            self.charm.set_unit_status(
                WaitingStatus("Waiting for the database to be stopped in all units")
            )
            logger.debug("Deferring on_async_relation_changed: not all units stopped.")
            event.defer()
            return

        if self._wait_for_standby_leader(event):
            return

        # Update the asynchronous replication configuration and start the database.
        self.charm.update_config()
        if not self.charm._patroni.start_patroni():
            raise Exception("Failed to start patroni service.")

        self._handle_database_start(event)

    def _on_async_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Set a flag to avoid setting a wrong status message on relation broken event handler."""
        # This is needed because of https://bugs.launchpad.net/juju/+bug/1979811.
        if event.departing_unit == self.charm.unit and self.charm._peers is not None:
            self.charm.unit_peer_data.update({"departing": "True"})

    def _on_async_relation_joined(self, _) -> None:
        """Publish this unit address in the relation data."""
        # store unit address in relation data
        self._relation.data[self.charm.unit].update({"unit-address": self._unit_ip})  # type: ignore

        # Set the counter for new units.
        highest_promoted_cluster_counter = self._get_highest_promoted_cluster_counter_value()
        if highest_promoted_cluster_counter != "0":
            self.charm.unit_peer_data.update({
                "unit-promoted-cluster-counter": highest_promoted_cluster_counter
            })

    def _on_create_replication(self, event: ActionEvent) -> None:
        """Set up asynchronous replication between two clusters."""
        if self._get_primary_cluster() is not None:
            event.fail("There is already a replication set up.")
            return

        if self._relation.name == REPLICATION_CONSUMER_RELATION:  # type: ignore
            event.fail("This action must be run in the cluster where the offer was created.")
            return

        if not self._handle_replication_change(event):
            return

        # Set the replication name in the relation data.
        self._relation.data[self.charm.app].update({"name": event.params["name"]})  # type: ignore

        # Set the status.
        self.charm.set_unit_status(MaintenanceStatus("Creating replication..."))

    def promote_to_primary(self, event: ActionEvent) -> None:
        """Promote this cluster to the primary cluster."""
        if (
            self.charm.app.status.message != READ_ONLY_MODE_BLOCKING_MESSAGE
            and self._get_primary_cluster() is None
        ):
            event.fail(
                "No primary cluster found. Run `create-replication` action in the cluster where the offer was created."
            )
            return

        if not self._handle_replication_change(event):
            return

        # Set the status.
        self.charm.set_unit_status(MaintenanceStatus("Creating replication..."))

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        """Update the internal secret when the relation secret changes."""
        relation = self._relation
        if relation is None:
            logger.debug("Early exit on_secret_changed: No relation found.")
            return

        if (
            relation.name == REPLICATION_OFFER_RELATION
            and event.secret.label == f"{PEER}.{self.model.app.name}.app"
        ):
            logger.info("Internal secret changed, updating relation secret")
            if not (secret := self._get_secret()):
                logger.debug("Defer on_secret_changed: Secret not created yet")
                event.defer()
                return
            secret.grant(relation)
            primary_cluster_data = {
                "endpoint": self._primary_cluster_endpoint,
                "secret-id": secret.id,
            }
            relation.data[self.charm.app]["primary-cluster-data"] = json.dumps(
                primary_cluster_data
            )
            return

        if relation.name == REPLICATION_CONSUMER_RELATION and event.secret.label == SECRET_LABEL:
            logger.info("Relation secret changed, updating internal secret")
            if not self._update_internal_secret():
                logger.debug("Secret not found, deferring event")
                event.defer()

    @property
    def _primary_cluster_endpoint(self) -> str | None:
        """Return the endpoint from one of the sync-standbys, or from the primary if there is no sync-standby."""
        sync_standby_names = self.charm._patroni.get_sync_standby_names()
        if len(sync_standby_names) > 0:
            unit = self.model.get_unit(sync_standby_names[0])
            return self.charm._get_unit_ip(unit, self._relation.name)  # type: ignore
        return self.charm._get_unit_ip(self.charm.unit, self._relation.name)  # type: ignore

    def _re_emit_async_relation_changed_event(self) -> None:
        """Re-emit the async relation changed event."""
        if relation := self._relation:
            getattr(self.charm.on, f"{relation.name.replace('-', '_')}_relation_changed").emit(
                relation,
                app=relation.app,
                unit=next(unit for unit in relation.units if unit.app == relation.app),
            )

    def _reinitialise_pgdata(self) -> None:
        """Reinitialise the data folder."""
        paths = [
            "/var/snap/charmed-postgresql/common/data/archive",
            POSTGRESQL_DATA_PATH,
            "/var/snap/charmed-postgresql/common/data/logs",
            "/var/snap/charmed-postgresql/common/data/temp",
        ]
        path = None
        try:
            for path in paths:
                path_object = Path(path)
                if path_object.exists() and path_object.is_dir():
                    for item in os.listdir(path):
                        item_path = os.path.join(path, item)
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.remove(item_path)
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
        except OSError as e:
            raise Exception(f"Failed to remove contents from {path} with error: {e!s}") from e

    @property
    def _relation(self) -> Relation | None:
        """Return the relation object."""
        for relation in [
            self.model.get_relation(REPLICATION_OFFER_RELATION),
            self.model.get_relation(REPLICATION_CONSUMER_RELATION),
        ]:
            if relation is not None:
                return relation

    def set_app_status(self) -> None:
        """Set the app status."""
        if self.charm.refresh is not None and self.charm.refresh.app_status_higher_priority:
            self.charm.app.status = self.charm.refresh.app_status_higher_priority
            return
        if self.charm._peers is None:
            return
        if self.charm._peers.data[self.charm.app].get("promoted-cluster-counter") == "0":
            self.charm.app.status = BlockedStatus(READ_ONLY_MODE_BLOCKING_MESSAGE)
            return
        if self._relation is None:
            self.charm.app.status = ActiveStatus()
            return
        primary_cluster = self._get_primary_cluster()
        if primary_cluster is None:
            self.charm.app.status = ActiveStatus()
        else:
            self.charm.app.status = ActiveStatus(
                "Primary" if self.charm.app == primary_cluster else "Standby"
            )

    def _stop_database(self, event: RelationChangedEvent) -> bool:
        """Stop the database."""
        if not self.charm.is_unit_stopped and not self._is_following_promoted_cluster():
            if not self.charm.unit.is_leader() and not os.path.exists(POSTGRESQL_DATA_PATH):
                logger.debug("Early exit on_async_relation_changed: following promoted cluster.")
                return False

            try:
                for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
                    with attempt:
                        if not self.charm._patroni.stop_patroni():
                            raise Exception("Failed to stop patroni service.")
            except RetryError:
                logger.debug("Deferring on_async_relation_changed: patroni hasn't stopped yet.")
                event.defer()
                return False

            if self.charm.unit.is_leader():
                # Remove the "cluster_initialised" flag to avoid self-healing in the update status hook.
                self.charm.app_peer_data.update({"cluster_initialised": ""})
                if not self._configure_standby_cluster(event):
                    return False

            # Remove and recreate the data folder to enable replication of the data from the
            # primary cluster.
            logger.info("Removing and recreating data folder")
            self._reinitialise_pgdata()

            # Remove previous cluster information to make it possible to initialise a new cluster.
            logger.info("Removing previous cluster information")
            try:
                path = Path(f"{PATRONI_CONF_PATH}/raft")
                if path.exists() and path.is_dir():
                    shutil.rmtree(path)
            except OSError as e:
                raise Exception(
                    f"Failed to remove previous cluster information with error: {e!s}"
                ) from e

            self.charm.unit_peer_data.update({"stopped": "True"})

        return True

    def update_async_replication_data(self) -> None:
        """Updates the async-replication data, if the unit is the leader.

        This is used to update the standby units with the new primary information.
        """
        relation = self._relation
        if relation is None:
            return
        relation.data[self.charm.unit].update({"unit-address": self._unit_ip})
        if self.is_primary_cluster() and self.charm.unit.is_leader():
            self._update_primary_cluster_data()

    def _update_internal_secret(self) -> bool:
        # Update the secrets between the clusters.
        relation = self._relation
        primary_cluster_info = relation.data[relation.app].get("primary-cluster-data")  # type: ignore
        secret_id = (
            None
            if primary_cluster_info is None
            else json.loads(primary_cluster_info).get("secret-id")
        )
        try:
            secret = self.charm.model.get_secret(id=secret_id, label=SECRET_LABEL)
        except SecretNotFoundError:
            return False
        credentials = secret.peek_content()
        for key, password in credentials.items():
            user = key.split("-password")[0]
            self.charm.set_secret(APP_SCOPE, key, password)
            logger.debug("Synced %s password", user)
        return True

    def _update_primary_cluster_data(
        self,
        promoted_cluster_counter: int | None = None,
        system_identifier: str | None = None,
    ) -> None:
        """Update the primary cluster data."""
        async_relation = self._relation

        if promoted_cluster_counter is not None:
            for relation in [async_relation, self.charm._peers]:  # type: ignore
                relation.data[self.charm.app].update({  # type: ignore
                    "promoted-cluster-counter": str(promoted_cluster_counter)
                })

        # Update the data in the relation.
        primary_cluster_data = {"endpoint": self._primary_cluster_endpoint}

        # Retrieve the secrets that will be shared between the clusters.
        if async_relation.name == REPLICATION_OFFER_RELATION:  # type: ignore
            secret = self._get_secret()
            secret.grant(async_relation)  # type: ignore
            primary_cluster_data["secret-id"] = secret.id  # type: ignore

        if system_identifier is not None:
            primary_cluster_data["system-id"] = system_identifier

        async_relation.data[self.charm.app]["primary-cluster-data"] = json.dumps(  # type: ignore
            primary_cluster_data
        )

    def _wait_for_standby_leader(self, event: RelationChangedEvent) -> bool:
        """Wait for the standby leader to be up and running."""
        try:
            standby_leader = self.charm._patroni.get_standby_leader(check_whether_is_running=True)
        except RetryError:
            standby_leader = None
        if not self.charm.unit.is_leader() and standby_leader is None:
            if self.charm._patroni.is_member_isolated:
                self.charm._patroni.restart_patroni()
                self.charm.set_unit_status(
                    WaitingStatus("Restarting Patroni to rejoin the cluster")
                )
                logger.debug(
                    "Deferring on_async_relation_changed: restarting Patroni to rejoin the cluster."
                )
                event.defer()
                return True
            self.charm.set_unit_status(
                WaitingStatus("Waiting for the standby leader start the database")
            )
            logger.debug("Deferring on_async_relation_changed: standby leader hasn't started yet.")
            event.defer()
            return True
        return False
