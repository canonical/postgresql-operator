# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Health monitoring logic for PostgreSQL watcher.

Implements the health check requirements from the acceptance criteria:
- Direct psycopg2 connections (no pgbouncer)
- SELECT 1 query with timeout
- 3 retries with 7-second intervals
- TCP keepalive settings
- Only participates in failover with even number of PostgreSQL instances
"""

import logging
import time
from typing import TYPE_CHECKING

import psycopg2

if TYPE_CHECKING:
    from charm import PostgreSQLWatcherCharm

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

    def __init__(self, charm: "PostgreSQLWatcherCharm"):
        """Initialize the health checker.

        Args:
            charm: The PostgreSQL watcher charm instance.
        """
        self.charm = charm
        self._retry_count = DEFAULT_RETRY_COUNT
        self._retry_interval = DEFAULT_RETRY_INTERVAL_SECONDS
        self._query_timeout = DEFAULT_QUERY_TIMEOUT_SECONDS
        self._check_interval = DEFAULT_CHECK_INTERVAL_SECONDS
        self._last_health_results: dict[str, bool] = {}

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

    def check_all_endpoints(self, endpoints: list[str]) -> dict[str, bool]:
        """Test connectivity to all PostgreSQL endpoints.

        Args:
            endpoints: List of PostgreSQL unit IP addresses.

        Returns:
            Dictionary mapping endpoint IP to health status (True = healthy).
        """
        results = {}
        for endpoint in endpoints:
            results[endpoint] = self._check_endpoint_with_retries(endpoint)

        self._last_health_results = results
        return results

    def _check_endpoint_with_retries(self, endpoint: str) -> bool:
        """Check a single endpoint with retry logic.

        Per acceptance criteria: Repeat tests at least 3 times before
        deciding that an instance is no longer reachable, waiting 7 seconds
        between every try.

        Args:
            endpoint: PostgreSQL endpoint IP address.

        Returns:
            True if the endpoint is healthy, False otherwise.
        """
        for attempt in range(self._retry_count):
            try:
                if self._execute_health_query(endpoint):
                    logger.debug(f"Health check passed for {endpoint} on attempt {attempt + 1}")
                    return True
            except Exception as e:
                logger.warning(
                    f"Health check failed for {endpoint} on attempt {attempt + 1}: {e}"
                )

            # Wait before retry (unless this is the last attempt)
            if attempt < self._retry_count - 1:
                logger.debug(
                    f"Waiting {self._retry_interval}s before retry for {endpoint}"
                )
                time.sleep(self._retry_interval)

        logger.error(
            f"Endpoint {endpoint} unhealthy after {self._retry_count} attempts"
        )
        return False

    def _execute_health_query(self, endpoint: str) -> bool:
        """Execute SELECT 1 query with TCP keepalive and timeout.

        Per acceptance criteria:
        - Testing actual queries (SELECT 1)
        - Using direct and reserved connections (no pgbouncer)
        - Setting TCP keepalive to avoid hanging on dead connections
        - Setting query timeout

        Args:
            endpoint: PostgreSQL endpoint IP address.

        Returns:
            True if the query succeeds and returns 1.
        """
        connection = None
        try:
            # Connect directly to PostgreSQL port 5432 (not pgbouncer 6432)
            # Using the 'postgres' database which always exists
            connection = psycopg2.connect(
                host=endpoint,
                port=5432,
                dbname="postgres",
                user="watcher",
                # Note: password would come from relation secret
                # For health checks, we might use trust auth or a dedicated user
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
                # Execute simple health check query
                # Note: PostgreSQL doesn't have DUAL table like Oracle
                # SELECT 1 is the standard PostgreSQL health check
                cursor.execute("SELECT 1")
                result = cursor.fetchone()

                if result and result[0] == 1:
                    return True
                else:
                    logger.warning(f"Unexpected result from health check: {result}")
                    return False

        except psycopg2.OperationalError as e:
            # Connection failures, timeouts, etc.
            logger.debug(f"Operational error connecting to {endpoint}: {e}")
            raise
        except psycopg2.Error as e:
            # Other database errors
            logger.debug(f"Database error on {endpoint}: {e}")
            raise
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    logger.debug(f"Failed to close connection to {endpoint}")

    def should_participate_in_failover(self, pg_endpoint_count: int) -> bool:
        """Determine if watcher should participate in failover decision.

        Per acceptance criteria: Only contributing to the failover decision
        if there is an even number of PostgreSQL instances.

        Args:
            pg_endpoint_count: Number of PostgreSQL endpoints.

        Returns:
            True if watcher should participate in failover, False otherwise.
        """
        should_participate = pg_endpoint_count % 2 == 0
        logger.debug(
            f"Failover participation: {should_participate} "
            f"(PostgreSQL endpoints: {pg_endpoint_count})"
        )
        return should_participate

    def get_last_health_results(self) -> dict[str, bool]:
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
        return sum(1 for healthy in self._last_health_results.values() if healthy)

    def all_endpoints_healthy(self) -> bool:
        """Check if all endpoints were healthy in last check.

        Returns:
            True if all endpoints are healthy.
        """
        if not self._last_health_results:
            return False
        return all(self._last_health_results.values())

    def any_endpoint_healthy(self) -> bool:
        """Check if any endpoint was healthy in last check.

        Returns:
            True if at least one endpoint is healthy.
        """
        if not self._last_health_results:
            return False
        return any(self._last_health_results.values())
