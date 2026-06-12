#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General PostgreSQL charm events."""

import logging
from typing import TYPE_CHECKING

from ops import InstallEvent, Object, StartEvent

if TYPE_CHECKING:
    from single_kernel_postgresql.charms.abstract_charm import AbstractPostgreSQLCharm

logger = logging.getLogger(__name__)


class PostgreSQLEventsHandler(Object):
    """Class implementing PostgreSQL Charm events handling."""

    def __init__(self, charm: "AbstractPostgreSQLCharm") -> None:
        super().__init__(charm, key="postgresql_events")
        self.charm = charm

        # Charm events
        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)

    def _on_install(self, event: InstallEvent) -> None:
        """Event handler for install event."""
        logger.info("Handling install event")

    def _on_start(self, event: StartEvent) -> None:
        """Event handler for start event."""
        ...
