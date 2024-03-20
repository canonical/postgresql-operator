# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implements the state-machine.

1) First async replication relation is made: both units get blocked waiting for a leader
2) User runs the promote action against one of the clusters
3) The cluster moves leader and sets the async-replication data, marking itself as leader
4) The other units receive that new information and update themselves to become standby-leaders.
"""

import json
import logging
import os
import pwd
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, run
from typing import Dict, Optional, Set, Tuple

from ops.charm import (
    ActionEvent,
    CharmBase,
)
from ops.framework import Object
from ops.model import (
    Unit,
    WaitingStatus, MaintenanceStatus, ActiveStatus,
)
from tenacity import Retrying, stop_after_attempt, wait_fixed, RetryError

from constants import (
    APP_SCOPE,
    MONITORING_PASSWORD_KEY,
    PATRONI_CONF_PATH,
    POSTGRESQL_DATA_PATH,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    USER_PASSWORD_KEY,
)
from coordinator_ops import CoordinatedOpsManager

logger = logging.getLogger(__name__)


ASYNC_PRIMARY_RELATION = "async-primary"
ASYNC_REPLICA_RELATION = "async-replica"


class MoreThanOnePrimarySelectedError(Exception):
    """Represents more than one primary has been selected."""


class PostgreSQLAsyncReplication(Object):
    """Defines the async-replication management logic."""

    def __init__(self, charm: CharmBase, relation_name: str = ASYNC_PRIMARY_RELATION) -> None:
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.charm = charm
        self.restart_coordinator = CoordinatedOpsManager(charm, "restart", tag="_asyncreplica")
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_changed, self._on_primary_changed
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_changed, self._on_standby_changed
        )
        self.framework.observe(
            self.restart_coordinator.on.coordinator_requested, self._on_coordination_request
        )
        self.framework.observe(
            self.restart_coordinator.on.coordinator_approved, self._on_coordination_approval
        )

        # Departure events
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_departed, self._on_departure
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_departed, self._on_departure
        )
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_broken, self._on_departure
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_broken, self._on_departure
        )

        # Actions
        self.framework.observe(
            self.charm.on.promote_standby_cluster_action, self._on_promote_standby_cluster
        )

        # We treat both relations above as actually the same.
        # The big difference appears only at promote/demote actions
        self.relation_set = {
            *set(self.charm.model.relations[ASYNC_PRIMARY_RELATION]),
            *set(self.charm.model.relations[ASYNC_REPLICA_RELATION]),
        }

    @property
    def endpoint(self) -> str:
        """Assumes the endpoint is the same, disregard if we are a primary or standby cluster."""
        sync_standby_names = self.charm._patroni.get_sync_standby_names()
        if len(sync_standby_names) > 0:
            unit = self.model.get_unit(sync_standby_names[0])
            return self.charm._get_unit_ip(unit)
        else:
            return self.charm._get_unit_ip(self.charm.unit)

    def standby_endpoints(self) -> Set[str]:
        """Returns the set of IPs used by each standby unit with a /32 mask."""
        standby_endpoints = set()
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if not rel.data[unit].get("elected", None):
                    standby_endpoints.add("{}/32".format(str(rel.data[unit]["ingress-address"])))
                    if "pod-address" in rel.data[unit]:
                        standby_endpoints.add("{}/32".format(str(rel.data[unit]["pod-address"])))
        return standby_endpoints

    def get_primary_data(self) -> Optional[Dict[str, str]]:
        """Returns the primary info, if available and if the primary cluster is ready."""
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if "elected" in rel.data[unit] and unit.name == self.charm.unit.name:
                    # If this unit is the leader, then return None
                    return None

                if rel.data[unit].get("elected", None) and rel.data[unit].get(
                    "primary-cluster-ready", None
                ):
                    elected_data = json.loads(rel.data[unit]["elected"])
                    return {
                        "endpoint": str(elected_data["endpoint"]),
                        "monitoring-password": elected_data["monitoring-password"],
                        "replication-password": elected_data["replication-password"],
                        "rewind-password": elected_data["rewind-password"],
                        "superuser-password": elected_data["superuser-password"],
                    }
        return None

    def _all_units(self, relation):
        found_units = {*relation.units, self.charm.unit}
        logger.debug(f"Units found: {found_units}")
        return found_units

    def _all_replica_published_unit_ips(self) -> bool:
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if "elected" in rel.data[unit]:
                    # This is the leader unit, it will not publish its own unit address
                    continue
                if "unit-address" not in rel.data[unit]:
                    return False
        return True

    def _on_departure(self, _):
        for rel in [
            self.model.get_relation(ASYNC_REPLICA_RELATION),
            self.model.get_relation(ASYNC_PRIMARY_RELATION),
        ]:
            if not rel:  # if no relation exits, then it rel == None
                continue
            if "pod-address" in rel.data[self.charm.unit]:
                del rel.data[self.charm.unit]["pod-address"]
            if "elected" in rel.data[self.charm.unit]:
                del rel.data[self.charm.unit]["elected"]
            if "primary-cluster-ready" in rel.data[self.charm.unit]:
                del rel.data[self.charm.unit]["primary-cluster-ready"]
        if self.charm.unit.is_leader() and "promoted" in self.charm.app_peer_data:
            del self.charm.app_peer_data["promoted"]

        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
            with attempt:
                if not self.charm._patroni.stop_patroni():
                    raise Exception("Failed to stop patroni service.")
        self.charm.update_config()
        if not self.charm._patroni.start_patroni():
            raise Exception("Failed to start patroni service.")

    def _on_primary_changed(self, event):
        """Triggers a configuration change in the primary units."""
        primary_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not primary_relation:
            return
        self.charm.unit.status = MaintenanceStatus("configuring main cluster")
        logger.info("_on_primary_changed: primary_relation exists")

        primary = self._check_if_primary_already_selected()
        if not primary:
            # primary may not be available because the action of promoting a cluster was
            # executed way after the relation changes.
            # Defer it until
            logger.debug("defer _on_primary_changed: primary not present")
            event.defer()
            return
        logger.info("_on_primary_changed: primary cluster exists")

        if primary.name != self.charm.unit.name:
            # this unit is not the system leader
            logger.debug("early exit _on_primary_changed: unit is not the primary's leader")
            self.charm.unit.status = ActiveStatus()
            return
        logger.info("_on_primary_changed: unit is the primary's leader")

        if not self._all_replica_published_unit_ips():
            # We will have more events happening, no need for retrigger
            logger.debug("defer _on_primary_changed: not all replicas published pod details")
            event.defer()
            return
        logger.info("_on_primary_changed: all replicas published pod details")

        # This unit is the leader, generate  a new configuration and leave.
        # There is nothing to do for the leader.
        try:
            for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
                with attempt:
                    if not self.charm._patroni.stop_patroni():
                        raise Exception("Failed to stop patroni service.")
        except RetryError:
            logger.debug("defer _on_primary_changed: failed to stop the container")
            event.defer()
            return
        self.charm.update_config()
        if not self.charm._patroni.start_patroni():
            raise Exception("Failed to start patroni service.")

        # Retrigger the other units' async-replica-changed
        primary_relation.data[self.charm.unit]["primary-cluster-ready"] = "True"
        self.charm.unit.status = ActiveStatus()

    def _on_standby_changed(self, event):  # noqa C901
        """Triggers a configuration change."""
        replica_relation = self.model.get_relation(ASYNC_REPLICA_RELATION)
        if not replica_relation:
            return
        self.charm.unit.status = MaintenanceStatus("configuring standby cluster")
        logger.info("_on_standby_changed: replica relation available")

        primary = self._check_if_primary_already_selected()
        if not primary:
            return
        logger.info("_on_standby_changed: primary is present")

        # Check if we have already published unit-address. If not, then we are waiting
        # for the leader to catch all the unit ips and restart itself
        if "unit-address" not in replica_relation.data[self.charm.unit]:
            replica_relation.data[self.charm.unit]["unit-address"] = self.charm._unit_ip
            # Finish here and wait for the retrigger from the primary cluster
            event.defer()
            return
        logger.info("_on_standby_changed: unit-address published in own replica databag")

        primary_data = self.get_primary_data()
        if not primary_data:
            # We've made thus far.
            # However, the get_primary_data will return != None ONLY if the primary cluster
            # is ready and configured. Until then, we wait.
            event.defer()
            return
        logger.info("_on_standby_changed: primary cluster is ready")

        if "system-id" not in replica_relation.data[self.charm.unit]:
            system_identifier, error = self.get_system_identifier()
            if error is not None:
                raise Exception(f"Failed to get system identifier: {error}")
            replica_relation.data[self.charm.unit]["system-id"] = system_identifier

            if self.charm.unit.is_leader():
                self.charm.set_secret(
                    APP_SCOPE, MONITORING_PASSWORD_KEY, primary_data["monitoring-password"]
                )
                self.charm.set_secret(
                    APP_SCOPE, USER_PASSWORD_KEY, primary_data["superuser-password"]
                )
                self.charm.set_secret(
                    APP_SCOPE, REPLICATION_PASSWORD_KEY, primary_data["replication-password"]
                )
                self.charm.set_secret(
                    APP_SCOPE, REWIND_PASSWORD_KEY, primary_data["rewind-password"]
                )
                del self.charm._peers.data[self.charm.app]["cluster_initialised"]

        if "cluster_initialised" in self.charm._peers.data[self.charm.app]:
            return

        ################
        # Initiate restart logic
        ################

        # We need to:
        # 1) Stop all standby units
        # 2) Delete the k8s service
        # 3) Remove the pgdata folder (if the clusters are different)
        # 4) Start all standby units
        # For that, the peer leader must first stop its own service and then, issue a
        # coordination request to all units. All units ack that request once they all have
        # their service stopped.
        # Then, we get an approved coordination from the leader, which triggers the
        # steps 2-4.
        if self.charm.unit.is_leader() and not self.restart_coordinator.under_coordination:
            # The leader now requests a ack from each unit that they have stopped.
            self.restart_coordinator.coordinate()

        self.charm.unit.status = WaitingStatus("waiting for promotion of the main cluster")

    def _on_coordination_request(self, event):
        # Stop the service.
        # We need all replicas to be stopped, so we can remove the previous cluster info.
        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3), reraise=True):
            with attempt:
                if not self.charm._patroni.stop_patroni():
                    raise Exception("Failed to stop patroni service.")

        replica_relation = self.model.get_relation(ASYNC_REPLICA_RELATION)
        for unit in replica_relation.units:
            if "elected" not in replica_relation.data[unit]:
                continue
            elected_data = json.loads(replica_relation.data[unit]["elected"])
            if "system-id" not in elected_data:
                continue
            if replica_relation.data[self.charm.unit]["system-id"] != elected_data["system-id"]:
                if self.charm.unit.is_leader():
                    # Store current data in a ZIP file, clean folder and generate configuration.
                    logger.info("Creating backup of pgdata folder")
                    subprocess.check_call(
                        f"tar -zcf {POSTGRESQL_DATA_PATH}-{str(datetime.now()).replace(' ', '-').replace(':', '-')}.zip {POSTGRESQL_DATA_PATH}".split()
                    )
                logger.info("Removing and recreating pgdata folder")
                try:
                    path = Path(POSTGRESQL_DATA_PATH)
                    if path.exists() and path.is_dir():
                        shutil.rmtree(path)
                except OSError as e:
                    raise Exception(
                        f"Failed to remove contents of the data directory with error: {str(e)}"
                    )
                break
        self.restart_coordinator.acknowledge(event)

    def _on_coordination_approval(self, event):
        """Runs when the coordinator guaranteed all units have stopped."""
        self.charm.update_config()
        logger.info(
            "_on_coordination_approval: configuration done, waiting for restart of the service"
        )

        if self.charm.unit.is_leader():
            # We are ready to restart the service now: all peers have configured themselves.
            if not self.charm._patroni.start_patroni():
                raise Exception("Failed to start patroni service.")

            # Remove previous cluster information to make it possible to initialise a new cluster.
            logger.info("Removing previous cluster information")

            def demote():
                pw_record = pwd.getpwnam("snap_daemon")

                def result():
                    os.setgid(pw_record.pw_gid)
                    os.setuid(pw_record.pw_uid)

                return result

            process = run(
                [
                    "charmed-postgresql.patronictl",
                    "-c",
                    f"{PATRONI_CONF_PATH}/patroni.yaml",
                    "remove",
                    self.charm.cluster_name,
                ],
                input=f"{self.charm.cluster_name}\nYes I am aware\npostgresql-0\n".encode(),
                stdout=PIPE,
                stderr=PIPE,
                preexec_fn=demote(),
                timeout=10,
            )
            if process.returncode != 0:
                raise Exception(
                    f"Failed to remove previous cluster information with error: {process.stderr.decode()}"
                )
            self.charm._peers.data[self.charm.app]["cluster_initialised"] = "True"
        else:
            self.charm.unit.status = WaitingStatus("waiting for primary to be ready")
            event.defer()
            return

    def _get_primary_candidates(self):
        rel = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        return rel.units if rel else []

    def _check_if_primary_already_selected(self) -> Optional[Unit]:
        """Returns the unit if a primary is present."""
        result = None
        if not self.relation_set:
            return None
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if "elected" in rel.data[unit] and not result:
                    result = unit
                elif "elected" in rel.data[unit] and result:
                    raise MoreThanOnePrimarySelectedError
        return result

    def _on_promote_standby_cluster(self, event: ActionEvent) -> None:
        """Moves a standby cluster to a primary, if none is present."""
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            event.fail("Cluster not initialized yet.")
            return

        if not self.charm.unit.is_leader():
            event.fail("Not the charm leader unit.")
            return

        # Now, publish that this unit is the leader
        if not self.endpoint:
            event.fail("No relation found.")
            return
        primary_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not primary_relation:
            event.fail("No primary relation")
            return

        # Let the exception error the unit
        unit = self._check_if_primary_already_selected()
        if unit:
            event.fail(f"Cannot promote - {unit.name} is already primary: demote it first")
            return

        system_identifier, error = self.get_system_identifier()
        if error is not None:
            event.fail(f"Failed to get system identifier: {error}")
            return

        # If this is a standby-leader, then execute switchover logic
        primary_relation.data[self.charm.unit]["elected"] = json.dumps(
            {
                "endpoint": self.endpoint,
                "monitoring-password": self.charm.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
                "replication-password": self.charm._patroni.replication_password,
                "rewind-password": self.charm.get_secret(APP_SCOPE, REWIND_PASSWORD_KEY),
                "superuser-password": self.charm._patroni.superuser_password,
                "system-id": system_identifier,
            }
        )

        # Now, check if postgresql it had originally published its unit IP in the
        # replica relation databag. Delete it, if yes.
        replica_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not replica_relation or "unit-address" not in replica_relation.data[self.charm.unit]:
            return
        del replica_relation.data[self.charm.unit]["unit-address"]

    def get_system_identifier(self) -> Tuple[Optional[str], Optional[str]]:
        """Returns the PostgreSQL system identifier from this instance."""

        def demote():
            pw_record = pwd.getpwnam("snap_daemon")

            def result():
                os.setgid(pw_record.pw_gid)
                os.setuid(pw_record.pw_uid)

            return result

        process = run(
            [
                f'/snap/charmed-postgresql/current/usr/lib/postgresql/{self.charm._patroni.get_postgresql_version().split(".")[0]}/bin/pg_controldata',
                POSTGRESQL_DATA_PATH,
            ],
            stdout=PIPE,
            stderr=PIPE,
            preexec_fn=demote(),
        )
        if process.returncode != 0:
            return None, process.stderr.decode()
        system_identifier = [
            line
            for line in process.stdout.decode().splitlines()
            if "Database system identifier" in line
        ][0].split(" ")[-1]
        return system_identifier, None

    def update_async_replication_data(self) -> None:
        """Updates the async-replication data, if the unit is the leader.

        This is used to update the standby units with the new primary information.
        If the unit is not the leader, then the data is removed from its databag.
        """
        if "promoted" not in self.charm.app_peer_data:
            return

        primary_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if self.charm.unit.is_leader():
            system_identifier, error = self.get_system_identifier()
            if error is not None:
                raise Exception(f"Failed to get system identifier: {error}")
            primary_relation.data[self.charm.unit]["elected"] = json.dumps(
                {
                    "endpoint": self.endpoint,
                    "monitoring-password": self.charm.get_secret(
                        APP_SCOPE, MONITORING_PASSWORD_KEY
                    ),
                    "replication-password": self.charm._patroni._replication_password,
                    "rewind-password": self.charm.get_secret(APP_SCOPE, REWIND_PASSWORD_KEY),
                    "superuser-password": self.charm._patroni._superuser_password,
                    "system-id": system_identifier,
                }
            )
        else:
            primary_relation.data[self.charm.unit]["elected"] = ""
