#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import time

import pytest as pytest
from tenacity import Retrying, stop_after_attempt, stop_after_delay, wait_exponential, wait_fixed

from .adapters import JujuFixture
from .ha_tests.helpers import (
    change_patroni_setting,
)
from .jubilant_helpers import (
    DATABASE_APP_NAME,
    METADATA,
    change_primary_start_timeout,
    check_tls,
    check_tls_patroni_api,
    check_tls_replication,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    primary_changed,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
tls_certificates_app_name = "self-signed-certificates"
tls_channel = "1/stable"


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
def test_deploy_active(juju: JujuFixture, charm):
    """Build the charm and deploy it."""
    with juju.ext.fast_forward():
        juju.ext.model.deploy(
            charm,
            application_name=APP_NAME,
            num_units=3,
            config={"profile": "testing"},
        )
        # No wait between deploying charms, since we can't guarantee users will wait. Furthermore,
        # bundles don't wait between deploying charms.


@pytest.mark.abort_on_fail
def test_tls_enabled(juju: JujuFixture) -> None:
    """Test that TLS is enabled when relating to the TLS Certificates Operator."""
    with juju.ext.fast_forward():
        # Deploy TLS Certificates operator.
        juju.ext.model.deploy(tls_certificates_app_name, channel=tls_channel)

        # Relate it to the PostgreSQL to enable TLS.
        juju.ext.model.relate(
            f"{DATABASE_APP_NAME}:client-certificates", f"{tls_certificates_app_name}:certificates"
        )
        juju.ext.model.relate(
            f"{DATABASE_APP_NAME}:peer-certificates", f"{tls_certificates_app_name}:certificates"
        )
        juju.ext.model.wait_for_idle(status="active", timeout=1500, raise_on_error=False)

        # Wait for all units enabling TLS.
        for unit in juju.ext.model.applications[DATABASE_APP_NAME].units:
            assert check_tls(juju, unit.name, enabled=True)
            assert check_tls_patroni_api(juju, unit.name, enabled=True)

        # Test TLS being used by pg_rewind. To accomplish that, get the primary unit
        # and a replica that will be promoted to primary (this should trigger a rewind
        # operation when the old primary is started again).
        any_unit = juju.ext.model.applications[DATABASE_APP_NAME].units[0].name
        primary = get_primary(juju, any_unit)
        replica = next(
            unit.name
            for unit in juju.ext.model.applications[DATABASE_APP_NAME].units
            if unit.name != primary
        )

        # Check if TLS enabled for replication
        assert check_tls_replication(juju, primary, enabled=True)

        patroni_password = get_password("patroni")

        # Enable additional logs on the PostgreSQL instance to check TLS
        # being used in a later step and make the fail-over to happens faster.
        juju.ext.model.applications[DATABASE_APP_NAME].set_config({
            "logging_log_connections": "True"
        })
        juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", idle_period=30)
        change_primary_start_timeout(juju, primary, 0, patroni_password)

        # Pause Patroni so it doesn't wipe the custom changes
        change_patroni_setting(
            juju, "pause", True, patroni_password, use_random_unit=True, tls=True
        )

    with juju.ext.fast_forward("24h"):
        for attempt in Retrying(
            stop=stop_after_delay(60 * 5), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                # Promote the replica to primary.
                run_command_on_unit(
                    juju,
                    replica,
                    "sudo charmed-postgresql.pg-ctl -D /var/snap/charmed-postgresql/common/var/lib/postgresql/ promote",
                )

                # Check that the replica was promoted.
                host = get_unit_address(juju, replica)
                password = get_password()
                with db_connect(host, password) as connection:
                    connection.autocommit = True
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_is_in_recovery();")
                        in_recovery = cursor.fetchone()[0]
                        print(f"in_recovery: {in_recovery}")
                        assert not in_recovery
                connection.close()

        # Write some data to the initial primary (this causes a divergence
        # in the instances' timelines).
        host = get_unit_address(juju, primary)
        password = get_password()
        with db_connect(host, password) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE IF NOT EXISTS pgrewindtest (testcol INT);")
                cursor.execute("INSERT INTO pgrewindtest SELECT generate_series(1,1000);")
        connection.close()

        # Stop the initial primary by killing both Patroni and PostgreSQL OS processes.
        run_command_on_unit(
            juju,
            primary,
            "pkill --signal SIGKILL -f /snap/charmed-postgresql/current/usr/lib/postgresql/16/bin/postgres",
        )
        run_command_on_unit(
            juju,
            primary,
            "pkill --signal SIGKILL -f /snap/charmed-postgresql/[0-9]*/usr/bin/patroni",
        )

        # Check that the primary changed.
        assert primary_changed(juju, primary), "primary not changed"
        change_primary_start_timeout(juju, primary, 300, patroni_password)

        # Check the logs to ensure TLS is being used by pg_rewind.
        primary = get_primary(juju, primary)
        for attempt in Retrying(stop=stop_after_attempt(10), wait=wait_fixed(5), reraise=True):
            with attempt:
                logger.info("Trying to grep for rewind logs.")
                run_command_on_unit(
                    juju,
                    primary,
                    "grep 'connection authorized: user=rewind database=postgres SSL enabled' /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log",
                )

        change_patroni_setting(
            juju, "pause", False, patroni_password, use_random_unit=True, tls=True
        )

    with juju.ext.fast_forward():
        # Remove the relation.
        juju.ext.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:client-certificates", f"{tls_certificates_app_name}:certificates"
        )
        juju.ext.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:peer-certificates", f"{tls_certificates_app_name}:certificates"
        )
        # Add a sleep to avoid immediate passing of the idle check.
        time.sleep(100)
        juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        # Wait for all units disabling TLS.
        for unit in juju.ext.model.applications[DATABASE_APP_NAME].units:
            assert check_tls(juju, unit.name, enabled=False)
            assert check_tls_patroni_api(juju, unit.name, enabled=False)
