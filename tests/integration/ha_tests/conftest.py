#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import subprocess
from asyncio import gather

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import get_password, run_command_on_unit
from .helpers import (
    APPLICATION_NAME,
    ORIGINAL_RESTART_CONDITION,
    RESTART_CONDITION,
    app_name,
    change_patroni_setting,
    change_wal_settings,
    get_patroni_setting,
    get_postgresql_parameter,
    update_restart_condition,
)

DEFAULT_LXD_NETWORK = "lxdbr0"
RAW_DNSMASQ = """dhcp-option=3
dhcp-option=6"""

logger = logging.getLogger(__name__)


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    yield
    # Clear the written data at the end.
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await ops_test.model.applications[APPLICATION_NAME]
                .units[0]
                .run_action("clear-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to clear up continuous_writes table"


@pytest.fixture()
async def loop_wait(ops_test: OpsTest) -> None:
    """Temporary change the loop wait configuration."""
    # Change the parameter that makes Patroni wait for some more time before restarting PostgreSQL.
    initial_loop_wait = await get_patroni_setting(ops_test, "loop_wait")
    yield
    # Rollback to the initial configuration.
    patroni_password = await get_password(ops_test, "patroni")
    await change_patroni_setting(
        ops_test, "loop_wait", initial_loop_wait, patroni_password, use_random_unit=True
    )


@pytest.fixture(scope="module")
async def primary_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the primary start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    patroni_password = await get_password(ops_test, "patroni")
    initial_primary_start_timeout = await get_patroni_setting(ops_test, "primary_start_timeout")
    await change_patroni_setting(ops_test, "primary_start_timeout", 0, patroni_password)
    yield
    # Rollback to the initial configuration.
    await change_patroni_setting(
        ops_test,
        "primary_start_timeout",
        initial_primary_start_timeout,
        patroni_password,
        use_random_unit=True,
    )


@pytest.fixture()
async def reset_restart_condition(ops_test: OpsTest):
    """Resets service file delay on all units."""
    app = await app_name(ops_test)

    awaits = []
    for unit in ops_test.model.applications[app].units:
        awaits.append(update_restart_condition(ops_test, unit, RESTART_CONDITION))
    await gather(*awaits)

    yield

    awaits = []
    for unit in ops_test.model.applications[app].units:
        awaits.append(update_restart_condition(ops_test, unit, ORIGINAL_RESTART_CONDITION))
    await gather(*awaits)


@pytest.fixture()
async def wal_settings(ops_test: OpsTest) -> None:
    """Restore the WAL settings to the initial values."""
    # Get the value for each setting.
    initial_max_wal_size = await get_postgresql_parameter(ops_test, "max_wal_size")
    initial_min_wal_size = await get_postgresql_parameter(ops_test, "min_wal_size")
    initial_wal_keep_segments = await get_postgresql_parameter(ops_test, "wal_keep_segments")
    yield
    app = await app_name(ops_test)
    for unit in ops_test.model.applications[app].units:
        # Start Patroni if it was previously stopped.
        await run_command_on_unit(ops_test, unit.name, "snap start charmed-postgresql.patroni")
        patroni_password = await get_password(ops_test, "patroni")

        # Rollback to the initial settings.
        await change_wal_settings(
            ops_test,
            unit.name,
            initial_max_wal_size,
            initial_min_wal_size,
            initial_wal_keep_segments,
            patroni_password,
        )


def _lxd_network(name: str, subnet: str, external: bool = True):
    try:
        output = subprocess.run(
            [
                "sudo",
                "lxc",
                "network",
                "create",
                name,
                "--type=bridge",
                f"ipv4.address={subnet}",
                f"ipv4.nat={external}".lower(),
                "ipv6.address=none",
                "dns.mode=none",
            ],
            capture_output=True,
            check=True,
            encoding="utf-8",
        ).stdout
        logger.info(f"LXD network created: {output}")
        output = subprocess.run(
            ["sudo", "lxc", "network", "show", name],
            capture_output=True,
            check=True,
            encoding="utf-8",
        ).stdout
        logger.debug(f"LXD network status: {output}")

        if not external:
            subprocess.check_output([
                "sudo",
                "lxc",
                "network",
                "set",
                name,
                "raw.dnsmasq",
                RAW_DNSMASQ,
            ])

        subprocess.check_output(
            f"sudo ip link set up dev {name}".split(),
        )
    except subprocess.CalledProcessError as e:
        if "The network already exists" in e.stderr:
            logger.warning(f"LXD network {name} already created")
            return
        logger.error(f"Error creating LXD network {name} with: {e.returncode} {e.stderr}")
        raise


@pytest.fixture(scope="session", autouse=True)
def lxd():
    try:
        # Set all networks' dns.mode=none
        # We want to avoid check:
        # https://github.com/canonical/lxd/blob/
        #     762f7dc5c3dc4dbd0863a796898212d8fbe3f7c3/lxd/device/nic_bridged.go#L403
        # As described on:
        # https://discuss.linuxcontainers.org/t/
        #     error-failed-start-validation-for-device-enp3s0f0-instance
        #     -dns-name-net17-nicole-munoz-marketing-already-used-on-network/15586/22?page=2
        subprocess.run(
            [
                "sudo",
                "lxc",
                "network",
                "set",
                DEFAULT_LXD_NETWORK,
                "dns.mode=none",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Error creating LXD network {DEFAULT_LXD_NETWORK} with: {e.returncode} {e.stderr}"
        )
        raise

    _lxd_network("client", "10.0.0.1/24", True)
    _lxd_network("peers", "10.10.10.1/24", False)
    _lxd_network("isolated", "10.20.20.1/24", False)


@pytest.fixture(scope="module")
async def lxd_spaces(ops_test):
    await ops_test.juju("reload-spaces")
    await ops_test.juju("add-space", "client", "10.0.0.1/24")
    await ops_test.juju("add-space", "peers", "10.10.10.1/24")
    await ops_test.juju("add-space", "isolated", "10.20.20.1/24")


@pytest.hookimpl()
def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("CI", "true").lower() == "true":
        # Nothing to do, as this is a temp runner only
        return

    def __exec(cmd):
        try:
            subprocess.check_output(cmd.split())
        except subprocess.CalledProcessError as e:
            # Log and try to delete the next network
            logger.warning(f"Error deleting LXD network with: {e.returncode} {e.stderr}")

    for network in ["client", "peers"]:
        __exec(f"sudo lxc network delete {network}")

    __exec(f"sudo lxc network unset {DEFAULT_LXD_NETWORK} dns.mode")
