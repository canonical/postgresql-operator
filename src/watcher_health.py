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
from asyncio import as_completed, create_task, run, wait
from contextlib import suppress
from ssl import CERT_NONE, create_default_context
from typing import TYPE_CHECKING, Any

import psycopg2
from httpx import AsyncClient, HTTPError
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from cluster import ClusterMember
from constants import API_REQUEST_TIMEOUT, PATRONI_CLUSTER_STATUS_ENDPOINT

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

    def __init__(self, charm: "PostgresqlOperatorCharm"):
        """Initialize the health checker.

        Args:
            charm: The PostgreSQL operator charm instance.
        """
        self.charm = charm

    def check_all_endpoints(self, endpoints: list[str], password: str) -> dict[str, bool]:
        """Test connectivity to all PostgreSQL endpoints.

        WARNING: This method uses blocking time.sleep() for retry intervals
        (up to ~38s worst case with 2 endpoints). Only call from Juju actions,
        never from hook handlers.

        Args:
            endpoints: List of PostgreSQL unit IP addresses.
            password: Password for the watcher user.

        Returns:
            Dictionary mapping endpoint IP to health status data.
        """
        results: dict[str, bool] = {}
        for endpoint in endpoints:
            results[endpoint] = self._check_endpoint_with_retries(endpoint, password)

        self._last_health_results = results
        return results

    def _check_endpoint_with_retries(self, endpoint: str, password: str) -> bool:
        """Check a single endpoint with retry logic.

        Per acceptance criteria: Repeat tests at least 3 times before
        deciding that an instance is no longer reachable, waiting 7 seconds
        between every try.

        Args:
            endpoint: PostgreSQL endpoint IP address.
            password: Password for the watcher user.

        Returns:
            Dictionary with health status data.
        """
        with suppress(RetryError):
            for attempt in Retrying(
                stop=stop_after_attempt(DEFAULT_RETRY_COUNT),
                wait=wait_fixed(DEFAULT_RETRY_INTERVAL_SECONDS),
            ):
                with attempt:
                    if result := self._execute_health_query(endpoint, password):
                        logger.debug(f"Health check passed for {endpoint}")
                        return result
                    raise Exception(f"Cannot reach {endpoint}")

        logger.error(f"Endpoint {endpoint} unhealthy after {DEFAULT_RETRY_COUNT} attempts")
        return False

    def _execute_health_query(self, endpoint: str, password: str) -> bool:
        """Execute health check queries with TCP keepalive and timeout.

        Per acceptance criteria:
        - Testing actual queries (SELECT 1)
        - Using direct and reserved connections (no pgbouncer)
        - Setting TCP keepalive to avoid hanging on dead connections
        - Setting query timeout

        Args:
            endpoint: PostgreSQL endpoint IP address.
            password: Password for the watcher user.

        Returns:
            Dictionary with health info (is_in_recovery, etc.) or None if failed.
        """
        connection = None
        result = False
        try:
            # Connect directly to PostgreSQL port 5432 (not pgbouncer 6432)
            # Using the 'postgres' database which always exists
            with (
                psycopg2.connect(
                    host=endpoint,
                    port=5432,
                    dbname="postgres",
                    user="watcher",
                    password=password,
                    connect_timeout=DEFAULT_QUERY_TIMEOUT_SECONDS,
                    # TCP keepalive settings per acceptance criteria
                    keepalives=1,
                    keepalives_idle=TCP_KEEPALIVE_IDLE,
                    keepalives_interval=TCP_KEEPALIVE_INTERVAL,
                    keepalives_count=TCP_KEEPALIVE_COUNT,
                    # Set options for query timeout
                    options=f"-c statement_timeout={DEFAULT_QUERY_TIMEOUT_SECONDS * 1000}",
                ) as connection,
                connection.cursor() as cursor,
            ):
                # Query recovery status to determine primary vs replica
                cursor.execute("SELECT 1")
                result = True

        except psycopg2.Error as e:
            # Other database errors
            logger.debug(f"Database error on {endpoint}: {e}")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except psycopg2.Error as e:
                    logger.debug(f"Failed to close connection to {endpoint}: {e}")
        return result

    async def _httpx_get_request(self, url: str) -> dict[str, Any] | None:
        ssl_ctx = create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = CERT_NONE
        async with AsyncClient(timeout=API_REQUEST_TIMEOUT, verify=ssl_ctx) as client:
            try:
                return (await client.get(url)).raise_for_status().json()
            except (HTTPError, ValueError):
                return None

    async def _async_get_request(self, uri: str, endpoints: list[str]) -> dict[str, Any] | None:
        tasks = [
            create_task(self._httpx_get_request(f"https://{ip}:8008{uri}")) for ip in endpoints
        ]
        for task in as_completed(tasks):
            if result := await task:
                for task in tasks:
                    task.cancel()
                await wait(tasks)
                return result

    def parallel_patroni_get_request(
        self, uri: str, endpoints: list[str]
    ) -> dict[str, Any] | None:
        """Call all possible patroni endpoints in parallel."""
        return run(self._async_get_request(uri, endpoints))

    def cluster_status(self, endpoints: list[str]) -> list[ClusterMember]:
        """Query the cluster status."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        if response := self.parallel_patroni_get_request(
            f"/{PATRONI_CLUSTER_STATUS_ENDPOINT}", endpoints
        ):
            logger.debug("API cluster_status: %s", response["members"])
            return response["members"]
        return []
