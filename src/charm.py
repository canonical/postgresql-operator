# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for PostgreSQL."""

import logging
from functools import cached_property

import charm_refresh
import ops
from charms.grafana_agent.v0.cos_agent import COSAgentProvider, ProtocolNotFoundError
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops_tracing import Tracing, set_destination
from single_kernel_postgresql.charms.vm_charm import PostgreSQLVMCharm
from single_kernel_postgresql.config.literals import (
    METRICS_PORT,
    PEER_RELATION,
    PGBACKREST_METRICS_PORT,
    TRACING_PROTOCOL,
    TRACING_RELATION_NAME,
)
from single_kernel_postgresql.core.config import CharmConfig

logger = logging.getLogger(__name__)


def charm_tracing_config(endpoint_requirer) -> None:
    """Utility function to set tracing destination."""
    if not endpoint_requirer.is_ready():
        return

    try:
        if not (endpoint := endpoint_requirer.get_tracing_endpoint(TRACING_PROTOCOL)):
            return
    except ProtocolNotFoundError:
        logger.warning(
            "Endpoint for tracing wasn't provided as tracing backend isn't ready yet. If grafana-agent isn't connected to a tracing backend, integrate it. Otherwise this issue should resolve itself in a few events."
        )
        return

    endpoint = f"{endpoint}/v1/traces"
    if endpoint.startswith("https://"):
        # if endpoint is https BUT we don't have a server_cert yet:
        # disable charm tracing until we do to prevent tls errors
        logger.warning("Cannot send traces to an https endpoint without a certificate.")
        return
    set_destination(endpoint, None)


class PostgresqlOperatorCharm(PostgreSQLVMCharm):
    """Charmed Operator for the PostgreSQL database."""

    config_type = CharmConfig

    # Prefer the dash (-) option name with the underscore (_) fallback; the lib
    # CharmState.config reads the same convention through its own cached property.
    @cached_property
    def config(self) -> CharmConfig:
        """Return a config instance validated and parsed using the provided pydantic class."""
        config = {
            config_option: self.model.config.get(
                config_option.replace("_", "-"), self.model.config.get(config_option)
            )
            for config_option in self.config_type.keys()  # noqa: SIM118
        }
        config = {k: v for k, v in config.items() if v is not None}
        return self.config_type(**config)

    def __init__(self, *args):
        super().__init__(
            *args,
            restart_lock_acquire=lambda: self.on[
                str(self.restart_manager.name)
            ].acquire_lock.emit(),
        )

        # Show logger name (module name) in logs.
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, ops.log.JujuLogHandler):
                handler.setFormatter(logging.Formatter("{name}:{message}", style="{"))

        # The rolling-ops manager is a charm lib; it stays charm-side and its
        # callback lands in the lib events handler.
        self.restart_manager = RollingOpsManager(
            charm=self,
            relation="restart",
            callback=self.postgresql_events_handler._restart,
        )

        # COS requirers (charm libs stay charm-side).
        self._grafana_agent = COSAgentProvider(
            self,
            metrics_endpoints=[
                {"path": "/metrics", "port": int(METRICS_PORT)},
                {"path": "/metrics", "port": int(PGBACKREST_METRICS_PORT)},
            ],
            scrape_configs=self.patroni_scrape_config,
            refresh_events=[
                self.on[PEER_RELATION].relation_changed,
                self.on.secret_changed,
                self.on.secret_remove,
            ],
            log_slots=[f"{charm_refresh.snap_name()}:logs"],
            tracing_protocols=[TRACING_PROTOCOL],
        )
        self.tracing = Tracing(self, tracing_relation_name=TRACING_RELATION_NAME)
        charm_tracing_config(self._grafana_agent)


if __name__ == "__main__":
    from ops.main import main

    main(PostgresqlOperatorCharm)
