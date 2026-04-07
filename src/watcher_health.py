# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Health monitoring logic for PostgreSQL watcher.

Implements the health check requirements from the acceptance criteria:
- Direct psycopg2 connections (no pgbouncer)
- SELECT 1 query with timeout
- 3 retries with 7-second intervals
- TCP keepalive settings
The watcher user and password are automatically provisioned by the PostgreSQL charm
when the watcher relation is established. The password is shared via a Juju secret.
"""

import logging
import time
from typing import TYPE_CHECKING, Any

import psycopg2

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

# Default health check configuration
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_INTERVAL_SECONDS = 7
DEFAULT_QUERY_TIMEOUT_SECONDS = 5
DEFAULT_CHECK_INTERVAL_SECONDS = 10

# TCP keepalive settings to detect dead connections quickly
TCP_KEEPALIVE_IDLE = 1  # Start keepalive probes after 1 second of idle
TCP_KEEPALIVE_INTERVAL = 1  # Send keepalive probes every 1 second
TCP_KEEPALIVE_COUNT = 3  # Consider connection dead after 3 failed probes


class HealthChecker:
    """Monitors PostgreSQL cluster health via direct database connections."""

    def __init__(self, charm: "PostgresqlOperatorCharm", password_getter=None):
        """Initialize the health checker.

        Args:
            charm: The PostgreSQL operator charm instance.
            password_getter: Callable that returns the watcher password.
        """
        self.charm = charm
        self._password_getter = password_getter
        self._retry_count = DEFAULT_RETRY_COUNT
        self._retry_interval = DEFAULT_RETRY_INTERVAL_SECONDS
        self._query_timeout = DEFAULT_QUERY_TIMEOUT_SECONDS
        self._check_interval = DEFAULT_CHECK_INTERVAL_SECONDS
        self._last_health_results: dict[str, dict[str, Any]] = {}

    def update_config(
        self,
        interval: int | None = None,
        timeout: int | None = None,
        retries: int | None = None,
        retry_interval: int | None = None,
    ) -> None:
        """Update health check configuration.

        Args:
            interval: Health check interval in seconds.
            timeout: Query timeout in seconds.
            retries: Number of retries before marking unhealthy.
            retry_interval: Wait time between retries in seconds.
        """
        if interval is not None:
            self._check_interval = interval
        if timeout is not None:
            self._query_timeout = timeout
        if retries is not None:
            self._retry_count = retries
        if retry_interval is not None:
            self._retry_interval = retry_interval

        logger.info(
            f"Health check config updated: interval={self._check_interval}s, "
            f"timeout={self._query_timeout}s, retries={self._retry_count}, "
            f"retry_interval={self._retry_interval}s"
        )

    def check_all_endpoints(self, endpoints: list[str]) -> dict[str, dict[str, Any]]:
        """Test connectivity to all PostgreSQL endpoints.

        WARNING: This method uses blocking time.sleep() for retry intervals
        (up to ~38s worst case with 2 endpoints). Only call from Juju actions,
        never from hook handlers.

        Args:
            endpoints: List of PostgreSQL unit IP addresses.

        Returns:
            Dictionary mapping endpoint IP to health status data.
        """
        results: dict[str, dict[str, Any]] = {}
        for endpoint in endpoints:
            results[endpoint] = self._check_endpoint_with_retries(endpoint)

        self._last_health_results = results
        return results

    def _check_endpoint_with_retries(self, endpoint: str) -> dict[str, Any]:
        """Check a single endpoint with retry logic.

        Per acceptance criteria: Repeat tests at least 3 times before
        deciding that an instance is no longer reachable, waiting 7 seconds
        between every try.

        Args:
            endpoint: PostgreSQL endpoint IP address.

        Returns:
            Dictionary with health status data.
        """
        for attempt in range(self._retry_count):
            result = self._execute_health_query(endpoint)
            if result:
                logger.debug(f"Health check passed for {endpoint} on attempt {attempt + 1}")
                return result

            # Wait before retry (unless this is the last attempt)
            if attempt < self._retry_count - 1:
                logger.debug(f"Waiting {self._retry_interval}s before retry for {endpoint}")
                time.sleep(self._retry_interval)

        logger.error(f"Endpoint {endpoint} unhealthy after {self._retry_count} attempts")
        return {"healthy": False}

    def _execute_health_query(self, endpoint: str) -> dict[str, Any] | None:
        """Execute health check queries with TCP keepalive and timeout.

        Per acceptance criteria:
        - Testing actual queries (SELECT 1)
        - Using direct and reserved connections (no pgbouncer)
        - Setting TCP keepalive to avoid hanging on dead connections
        - Setting query timeout

        Args:
            endpoint: PostgreSQL endpoint IP address.

        Returns:
            Dictionary with health info (is_in_recovery, etc.) or None if failed.
        """
        connection = None
        try:
            # Connect directly to PostgreSQL port 5432 (not pgbouncer 6432)
            # Using the 'postgres' database which always exists
            watcher_password = self._password_getter() if self._password_getter else None
            connection = psycopg2.connect(
                host=endpoint,
                port=5432,
                dbname="postgres",
                user="watcher",
                password=watcher_password,
                connect_timeout=self._query_timeout,
                # TCP keepalive settings per acceptance criteria
                keepalives=1,
                keepalives_idle=TCP_KEEPALIVE_IDLE,
                keepalives_interval=TCP_KEEPALIVE_INTERVAL,
                keepalives_count=TCP_KEEPALIVE_COUNT,
                # Set options for query timeout
                options=f"-c statement_timeout={self._query_timeout * 1000}",
            )

            # Use autocommit to avoid transaction overhead
            connection.autocommit = True

            with connection.cursor() as cursor:
                # Query recovery status to determine primary vs replica
                cursor.execute("SELECT pg_is_in_recovery()")
                is_in_recovery = cursor.fetchone()[0]
                return {"healthy": True, "is_in_recovery": is_in_recovery}

        except psycopg2.OperationalError as e:
            # Connection failures, timeouts, etc.
            logger.debug(f"Operational error connecting to {endpoint}: {e}")
            return None
        except psycopg2.Error as e:
            # Other database errors
            logger.debug(f"Database error on {endpoint}: {e}")
            return None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except psycopg2.Error as e:
                    logger.debug(f"Failed to close connection to {endpoint}: {e}")

    def get_last_health_results(self) -> dict[str, dict[str, Any]]:
        """Get the last health check results.

        Returns:
            Dictionary mapping endpoint IP to health status.
        """
        return self._last_health_results.copy()

    def get_healthy_endpoint_count(self) -> int:
        """Get the count of healthy endpoints from last check.

        Returns:
            Number of healthy endpoints.
        """
        return sum(1 for res in self._last_health_results.values() if res.get("healthy"))

    def all_endpoints_healthy(self) -> bool:
        """Check if all endpoints were healthy in last check.

        Returns:
            True if all endpoints are healthy.
        """
        if not self._last_health_results:
            return False
        return all(res.get("healthy") for res in self._last_health_results.values())

    def any_endpoint_healthy(self) -> bool:
        """Check if any endpoint was healthy in last check.

        Returns:
            True if at least one endpoint is healthy.
        """
        if not self._last_health_results:
            return False
        return any(res.get("healthy") for res in self._last_health_results.values())
