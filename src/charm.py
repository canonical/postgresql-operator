# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Machine Operator for PostgreSQL."""

import logging

# First platform-specific import; on wrong architectures fall back to a
# blocked warning charm (the wheel for this arch is unavailable).
try:
    from single_kernel_postgresql.charms.vm_charm import PostgreSQLVMCharm
except ModuleNotFoundError:
    from ops.main import main
    from single_kernel_postgresql.utils.arch import (
        WrongArchitectureWarningCharm,
        is_wrong_architecture,
    )

    if is_wrong_architecture() and __name__ == "__main__":
        main(WrongArchitectureWarningCharm)
    raise

import charm_refresh
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops_tracing import Tracing
from single_kernel_postgresql.config.literals import (
    METRICS_PORT,
    PEER_RELATION,
    PGBACKREST_METRICS_PORT,
    TRACING_PROTOCOL,
    TRACING_RELATION_NAME,
)
from single_kernel_postgresql.events.tls_transfer import TLSTransfer
from single_kernel_postgresql.utils.tracing import charm_tracing_config

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(PostgreSQLVMCharm):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(
            *args,
            restart_lock_acquire=lambda: self.on[
                str(self.restart_manager.name)
            ].acquire_lock.emit(),
        )
        self.restart_manager = RollingOpsManager(
            charm=self,
            relation="restart",
            callback=self._restart,
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
        self.tls_transfer = TLSTransfer(self, PEER_RELATION)
        charm_tracing_config(self._grafana_agent)


if __name__ == "__main__":
    from ops.main import main

    main(PostgresqlOperatorCharm)
