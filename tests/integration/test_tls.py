#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt

from tests.helpers import METADATA
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_tls,
    check_tls_patroni_api,
    restart_machine,
    run_command_on_unit,
)

APP_NAME = METADATA["name"]
# STORAGE_MOUNTPOINT = METADATA
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
        # No wait between deploying charms, since we can't guarantee users will wait. Furthermore,
        # bundles don't wait between deploying charms.


@pytest.mark.tls_tests
async def test_tls_enabled(ops_test: OpsTest) -> None:
    """Test that TLS is enabled when relating to the TLS Certificates Operator."""
    async with ops_test.fast_forward():
        # Deploy TLS Certificates operator.
        config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
        await ops_test.model.deploy(TLS_CERTIFICATES_APP_NAME, channel="edge", config=config)

        # Relate it to the PostgreSQL to enable TLS.
        await ops_test.model.relate(DATABASE_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Wait for all units enabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=True)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)

        # Remove the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:certificates", f"{TLS_CERTIFICATES_APP_NAME}:certificates"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        # Wait for all units disabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=False)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=False)


@pytest.mark.tls_tests
async def test_restart_machines(ops_test: OpsTest) -> None:
    async with ops_test.fast_forward():
        # Relate it to the PostgreSQL to enable TLS.
        await ops_test.model.relate(DATABASE_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Wait for all units enabling TLS.
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        assert await check_tls(ops_test, unit.name, enabled=True)
        assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)

    for attempt in Retrying(stop=stop_after_attempt(10)):
        with attempt:
            # Restart the machine of each unit.
            issue_found = False
            for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
                await restart_machine(ops_test, unit.name)
                # result = await run_command_on_unit(
                #     ops_test, unit.name, "ls -al /var/lib/postgresql/data"
                # )
                # print(f"{attempt.retry_state.attempt_number} - result for {unit.name}: {result}")
                result = await run_command_on_unit(ops_test, unit.name, "lsblk")
                print(f"{attempt.retry_state.attempt_number} - result for {unit.name}: {result}")
                if "/var/lib/postgresql/data" not in result:
                    print("issue found!!!")
                    issue_found = True
                    break

            if issue_found:
                break

            assert (
                False
            ), "Couldn't reproduce the issue from https://bugs.launchpad.net/juju/+bug/1999758"

    # Wait for all units enabling TLS.
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        assert await check_tls(ops_test, unit.name, enabled=True)
        assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)
