#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the charm on IPv6-enabled deployments.

On an IPv6 cloud the peer relation binding can resolve to an IPv6
address; the charm must then bracket that address in every URL it builds
for the Patroni REST API (#1928). The scenario under test is selected
with the PG_IP_FAMILY environment variable:

- "ipv6": the peer relation is bound to a Juju space holding only the
  IPv6 subnet (PG_IPV6_SUBNET), so the charm's own cluster networking is
  purely IPv6;
- "ipv4": the deployment only has IPv4 addresses;
- "dual": both families are present and Juju picks one.

The PG_IPV6_SUBNET environment variable must name the IPv6 subnet that
the LXD bridge hands out (its subnet must exist on the host).
"""

import json
import os

import pytest

from .adapters import JujuFixture
from .jubilant_helpers import DATABASE_APP_NAME, get_primary, run_command_on_unit

IP_FAMILY = os.environ.get("PG_IP_FAMILY", "dual")
V6_SUBNET = os.environ.get("PG_IPV6_SUBNET", "fd42:1928:642::/64")


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
def test_deploy(juju: JujuFixture, charm: str):
    """Deploy the charm and wait for it to reach active/idle."""
    if IP_FAMILY == "ipv6":
        # Bind the peer relation to a space that only contains the IPv6
        # subnet: the charm then publishes and queries IPv6 addresses
        # for all of its own cluster networking.
        existing = json.loads(juju.cli("spaces", "--format", "json"))
        if not any(space["name"] == "ipv6" for space in existing["spaces"]):
            juju.cli("add-space", "ipv6", V6_SUBNET)
        juju.ext.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            config={"profile": "testing"},
            bind={"database-peers": "ipv6"},
        )
    else:
        juju.ext.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            config={"profile": "testing"},
        )
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)
    assert juju.ext.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
def test_peer_binding_address_family(juju: JujuFixture):
    """The peer relation binding must resolve to the selected family."""
    output = run_command_on_unit(
        juju, f"{DATABASE_APP_NAME}/0", "network-get database-peers --format json"
    )
    binding = json.loads(output)
    addresses = [
        address["value"]
        for entry in binding["bind-addresses"]
        for address in entry.get("addresses", [])
        if address.get("value")
    ]
    assert addresses, "no addresses on the peer relation binding"
    if IP_FAMILY == "ipv6":
        subnet_prefix = V6_SUBNET.split("::")[0]
        assert all(address.lower().startswith(subnet_prefix) for address in addresses), (
            f"expected IPv6 addresses in {V6_SUBNET}, got {addresses}"
        )
    elif IP_FAMILY == "ipv4":
        assert all(":" not in address for address in addresses), (
            f"expected IPv4-only addresses, got {addresses}"
        )


@pytest.mark.abort_on_fail
def test_primary_is_reachable(juju: JujuFixture):
    """The get-primary action must query the Patroni REST API successfully.

    That action goes through the charm's own Patroni API URL building:
    the exact path that crashed on IPv6 clouds before the fix (#1928).
    """
    assert get_primary(juju, f"{DATABASE_APP_NAME}/0")
