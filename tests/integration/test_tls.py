#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_exponential

from tests.helpers import METADATA
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_tls,
    check_tls_patroni_api,
    db_connect,
    enable_connections_logging,
    get_password,
    get_primary,
    get_unit_address,
    primary_changed,
    run_command_on_unit,
)

APP_NAME = METADATA["name"]
TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"


@pytest.mark.abort_on_fail
@pytest.mark.tls_tests
@pytest.mark.skip_if_deployed
async def test_deploy_active(ops_test: OpsTest):
    """Build the charm and deploy it."""
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm, resources={"patroni": "patroni.tar.gz"}, application_name=APP_NAME, num_units=3
        )
        await ops_test.juju("attach-resource", APP_NAME, "patroni=patroni.tar.gz")
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)


@pytest.mark.tls_tests
async def test_tls_enabled(ops_test: OpsTest) -> None:
    """Test that TLS is enabled when relating to the TLS Certificates Operator."""
    async with ops_test.fast_forward():
        await list_wal_files(ops_test, "before TLS charm")
        # Deploy TLS Certificates operator.
        config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
        await ops_test.model.deploy(TLS_CERTIFICATES_APP_NAME, channel="edge", config=config)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )
        await list_wal_files(ops_test, "before TLS relation")

        # Relate it to the PostgreSQL to enable TLS.
        await ops_test.model.relate(DATABASE_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
        await list_wal_files(ops_test, "before TLS checks")

        # Wait for all units enabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=True)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)
        await list_wal_files(ops_test, "before get primary")

        # Test TLS being used by pg_rewind. To accomplish that, get the primary unit
        # and a replica that will be promoted to primary (this should trigger a rewind
        # operation when the old primary is started again).
        any_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0].name
        primary = await get_primary(ops_test, any_unit)
        replica = [
            unit.name
            for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            if unit.name != primary
        ][0]
        await list_wal_files(ops_test, "before connections logging")

        # Enable additional logs on the PostgreSQL instance to check TLS
        # being used in a later step.
        enable_connections_logging(ops_test, primary)
        await list_wal_files(ops_test, "before promote")

        # Promote the replica to primary.
        await run_command_on_unit(
            ops_test,
            replica,
            "su -c '/usr/lib/postgresql/12/bin/pg_ctl -D /var/lib/postgresql/data/pgdata promote' postgres",
        )
        await list_wal_files(ops_test, "before write data")

        # Write some data to the initial primary (this causes a divergence
        # in the instances' timelines).
        host = get_unit_address(ops_test, primary)
        password = await get_password(ops_test, primary)
        with db_connect(host, password) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE pgrewindtest (testcol INT);")
                cursor.execute("INSERT INTO pgrewindtest SELECT generate_series(1,1000);")
        connection.close()
        await list_wal_files(ops_test, "before stop")

        # Stop the initial primary by killing both Patroni and PostgreSQL OS processes.
        await run_command_on_unit(
            ops_test, primary, "pkill --signal SIGKILL -f /usr/local/bin/patroni"
        )
        await run_command_on_unit(ops_test, primary, "pkill --signal SIGKILL -f postgres")
        await list_wal_files(ops_test, "before primary change check")

        # Check that the primary changed.
        assert await primary_changed(ops_test, primary), "primary not changed"
        await list_wal_files(ops_test, "before start")

        # Restart the initial primary and check the logs to ensure TLS is being used by pg_rewind.
        # It can take some time for the rewind operation to happen.
        await run_command_on_unit(ops_test, primary, "systemctl restart patroni")
        await list_wal_files(ops_test, "before rewind check")
        for attempt in Retrying(
            stop=stop_after_delay(60 * 5), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                logs = await run_command_on_unit(
                    ops_test, replica, "journalctl -u patroni.service"
                )
                await list_wal_files(ops_test, "before assert")
                assert (
                    "connection authorized: user=rewind database=postgres SSL enabled"
                    " (protocol=TLSv1.3, cipher=TLS_AES_256_GCM_SHA384, bits=256, compression=off)"
                    in logs
                ), "TLS is not being used on pg_rewind connections"

        # Remove the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:certificates", f"{TLS_CERTIFICATES_APP_NAME}:certificates"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        # Wait for all units disabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=False)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=False)


async def list_wal_files(ops_test: OpsTest, step: str):
    units = [unit.name for unit in ops_test.model.applications[DATABASE_APP_NAME].units]
    command = "ls -al /var/lib/postgresql/data/pgdata/pg_wal/"
    with open("wal_files.txt", "a+") as file:
        file.write(f"step: {step}\n")
        for unit in units:
            complete_command = f"run --unit {unit} -- {command}"
            return_code, stdout, stderr = await ops_test.juju(*complete_command.split())
            file.write(f"unit: {unit}\n")
            file.write(f"return_code: {return_code}\n")
            file.write(f"stdout: {stdout}\n")
            file.write(f"stderr: {stderr}\n")
            file.write("\n")
