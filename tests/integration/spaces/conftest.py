# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import subprocess

import jubilant
import pytest

DEFAULT_LXD_NETWORK = "lxdbr0"
RAW_DNSMASQ = """dhcp-option=3
dhcp-option=6"""

logger = logging.getLogger(__name__)


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
def lxd_spaces(juju: jubilant.Juju):
    juju.cli("reload-spaces")
    juju.cli("add-space", "client", "10.0.0.1/24")
    juju.cli("add-space", "peers", "10.10.10.1/24")
    juju.cli("add-space", "isolated", "10.20.20.1/24")


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
