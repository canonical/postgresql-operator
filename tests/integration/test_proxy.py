#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test: charm deploys and operates correctly behind an HTTP proxy.

Regression test for https://github.com/canonical/postgresql-operator/issues/1714.
When Juju model-config sets an HTTP proxy, proxy environment variables leak into
all unit processes. Patroni REST API calls (intra-cluster, on private IPs) must
bypass the proxy — otherwise the charm gets stuck in "awaiting start of the
primary".

Reproduces the exact scenario from the issue: both Juju model-config proxy
settings AND cloudinit-userdata writing proxy vars to /etc/environment, with
a real Squid proxy running on the LXD host.
"""

import logging
import subprocess
import textwrap

import pytest
import requests

from .adapters import JujuFixture, temp_model_fixture
from .jubilant_helpers import (
    DATABASE_APP_NAME,
    get_primary,
    get_unit_address,
)

logger = logging.getLogger(__name__)


def _get_lxd_bridge_ip() -> str:
    """Return the IP of the lxdbr0 bridge (proxy host reachable by containers)."""
    output = subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "lxdbr0"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return output.split("inet ", 1)[1].split("/")[0]


PROXY_HOST = _get_lxd_bridge_ip()
PROXY_URL = f"http://{PROXY_HOST}:3128"

CLOUDINIT_USERDATA = textwrap.dedent("""\
    #cloud-config
    write_files:
    - path: /etc/environment
      permissions: '0644'
      owner: root:root
      content: |
        http_proxy={proxy}
        https_proxy={proxy}
        HTTP_PROXY={proxy}
        HTTPS_PROXY={proxy}
        no_proxy=localhost,127.0.0.1,10.0.0.0/8
        NO_PROXY=localhost,127.0.0.1,10.0.0.0/8
""").format(proxy=PROXY_URL)


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    keep_models = bool(request.config.getoption("--keep-models"))
    with temp_model_fixture(
        keep=keep_models,
        config={
            "http-proxy": PROXY_URL,
            "https-proxy": PROXY_URL,
            "no-proxy": "127.0.0.1,localhost,::1",
            "cloudinit-userdata": CLOUDINIT_USERDATA,
        },
    ) as juju:
        yield juju


@pytest.mark.abort_on_fail
def test_deploy_with_proxy(juju: JujuFixture, charm: str):
    """Deploy PostgreSQL in a model with HTTP proxy configured."""
    juju.ext.model.deploy(
        charm,
        application_name=DATABASE_APP_NAME,
        num_units=3,
        config={"profile": "testing"},
    )
    juju.ext.model.set_config({"update-status-hook-interval": "10s"})
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)


def test_proxy_env_vars_present_on_units(juju: JujuFixture):
    """Verify the proxy env vars are set in /etc/environment (test precondition)."""
    unit_name = next(iter(juju.status().get_units(DATABASE_APP_NAME)))
    env_output = juju.ssh(unit_name, "cat /etc/environment")
    assert "HTTPS_PROXY" in env_output, (
        "Proxy env vars not found in /etc/environment — cloudinit-userdata not applied"
    )


def test_patroni_api_reachable(juju: JujuFixture):
    """Patroni REST API responds on every unit despite proxy env vars."""
    units = juju.status().get_units(DATABASE_APP_NAME)
    for unit_name in units:
        host = get_unit_address(juju, unit_name)
        result = requests.get(f"https://{host}:8008/health", verify=False)
        assert result.status_code == 200, f"Patroni API unreachable on {unit_name}"


def test_get_primary_works(juju: JujuFixture):
    """The get-primary action succeeds (exercises the charm's internal Patroni client)."""
    unit_name = next(iter(juju.status().get_units(DATABASE_APP_NAME)))
    primary = get_primary(juju, unit_name)
    assert primary, "get-primary returned empty result"
