#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Kubernetes Manager.

This managers is responsible for handling operations related to Kubernetes,
such as interacting with the Kubernetes API and also configuring Pebble to work with Kubernetes.
"""

import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope as AdvancedStatusesScope
from ops.pebble import CheckDict, Layer, LayerDict, ServiceDict

from single_kernel_postgresql.config.literals import (
    K8S_LDAP_SYNC_SERVICE_NAME,
    K8S_METRICS_SERVER_SERVICE_NAME,
    K8S_PGBACK_REST_SERVER_SERVICE_NAME,
    K8S_PGBACKREST_METRICS_SERVER_SERVICE_NAME,
    K8S_POSTGRESQL_SERVICE_NAME,
    K8S_ROTATE_LOGS_SERVICE_NAME,
    K8S_WORKLOAD_OS_GROUP,
    K8S_WORKLOAD_OS_USER,
    MONITORING_USER,
    ORIGINAL_PATRONI_ON_FAILURE_CONDITION,
    REPLICATION_USER,
    USER,
)
from single_kernel_postgresql.config.statuses import GeneralStatuses
from single_kernel_postgresql.core.state import CharmState
from single_kernel_postgresql.managers.base import BaseManager
from single_kernel_postgresql.utils import unit_name_to_pod_name
from single_kernel_postgresql.utils.postgresql import PostgreSQL as PostgreSQLClient
from single_kernel_postgresql.workload.k8s import K8sWorkload

logger = logging.getLogger(__name__)


class K8sManager(BaseManager):
    """PostgreSQL Kubernetes Manager.

    This manager is responsible for handling operations related to Kubernetes and Pebble.
    """

    def __init__(self, state: CharmState, workload: K8sWorkload, client: PostgreSQLClient):
        super().__init__(state, workload, "pebble_manager", client)
        self.workload: K8sWorkload = workload  # type: ignore[assignment]

    def update_pebble_layers(self, replan: bool = True) -> None:
        """Update the pebble layers to keep the health check URL up-to-date."""
        # Create a new config layer.
        new_layer = self._postgresql_layer()

        # Reconcile pebble
        self.workload.reconcile_pebble_layer(new_layer, replan)

    def _postgresql_layer(self) -> Layer:
        """Returns a Pebble configuration layer for PostgreSQL."""
        pod_name = unit_name_to_pod_name(self.state.peer.unit_name)
        layer_config = LayerDict({
            "summary": "postgresql + patroni layer",
            "description": "pebble config layer for postgresql + patroni",
            "services": {
                K8S_POSTGRESQL_SERVICE_NAME: ServiceDict({
                    "override": "replace",
                    "summary": "entrypoint of the postgresql + patroni image",
                    "command": f"patroni {self.workload.paths.data}/patroni.yml",
                    "startup": "enabled",
                    "on-failure": self.state.peer.patroni_on_failure_condition_override
                    or ORIGINAL_PATRONI_ON_FAILURE_CONDITION,
                    "user": K8S_WORKLOAD_OS_USER,
                    "group": K8S_WORKLOAD_OS_GROUP,
                    "environment": {
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: {self.state.application.cluster_name}}}",
                        "PATRONI_KUBERNETES_LEADER_LABEL_VALUE": "primary",
                        "PATRONI_KUBERNETES_NAMESPACE": self.state.model_name,
                        "PATRONI_KUBERNETES_USE_ENDPOINTS": "true",
                        "PATRONI_NAME": pod_name,
                        "PATRONI_SCOPE": self.state.application.cluster_name,
                        "PATRONI_REPLICATION_USERNAME": REPLICATION_USER,
                        "PATRONI_SUPERUSER_USERNAME": USER,
                    },
                }),
                K8S_PGBACK_REST_SERVER_SERVICE_NAME: ServiceDict({
                    "override": "replace",
                    "summary": "pgBackRest server",
                    "command": K8S_PGBACK_REST_SERVER_SERVICE_NAME,
                    "startup": "disabled",
                    "user": K8S_WORKLOAD_OS_USER,
                    "group": K8S_WORKLOAD_OS_GROUP,
                }),
                K8S_LDAP_SYNC_SERVICE_NAME: ServiceDict({
                    "override": "replace",
                    "summary": "synchronize LDAP users",
                    "command": "/start-ldap-synchronizer.sh",
                    "startup": "disabled",
                }),
                K8S_METRICS_SERVER_SERVICE_NAME: self._generate_metrics_service(),
                K8S_PGBACKREST_METRICS_SERVER_SERVICE_NAME: self._generate_pgbackrest_metrics_service(),
                K8S_ROTATE_LOGS_SERVICE_NAME: ServiceDict({
                    "override": "replace",
                    "summary": "rotate logs",
                    "command": "python3 /home/postgres/rotate_logs.py",
                    "startup": "disabled",
                }),
            },
            "checks": {
                K8S_POSTGRESQL_SERVICE_NAME: CheckDict({
                    "override": "replace",
                    "level": "ready",
                    "exec": {
                        "command": "python3 /scripts/self-signed-checker.py",
                        "user": K8S_WORKLOAD_OS_USER,
                        "environment": {
                            "ENDPOINT": f"{self.state.patroni_url}/health",
                        },
                    },
                })
            },
        })
        return Layer(layer_config)

    def _generate_metrics_service(self) -> ServiceDict:
        """Generate the metrics service definition."""
        return {
            "override": "replace",
            "summary": "postgresql metrics exporter",
            "command": "/start-exporter.sh",
            "startup": (
                "enabled" if self.state.application.monitoring_password is not None else "disabled"
            ),
            "after": [K8S_POSTGRESQL_SERVICE_NAME],
            "user": K8S_WORKLOAD_OS_USER,
            "group": K8S_WORKLOAD_OS_GROUP,
            "environment": {
                "DATA_SOURCE_NAME": (
                    f"user={MONITORING_USER} "
                    f"password={self.state.application.monitoring_password} "
                    "host=/var/run/postgresql port=5432 database=postgres"
                ),
            },
        }

    def _generate_pgbackrest_metrics_service(self) -> ServiceDict:
        """Generate the pgbackrest metrics service definition."""
        return {
            "override": "replace",
            "summary": "pgbackrest metrics exporter",
            "command": "/usr/bin/pgbackrest_exporter",
            "startup": "enabled",
            "after": [K8S_POSTGRESQL_SERVICE_NAME],
            "user": K8S_WORKLOAD_OS_USER,
            "group": K8S_WORKLOAD_OS_GROUP,
        }

    def get_statuses(
        self, scope: AdvancedStatusesScope, recompute: bool = False
    ) -> list[StatusObject]:
        """Compute the manager's statuses."""
        return [GeneralStatuses.ACTIVE_IDLE.value]
