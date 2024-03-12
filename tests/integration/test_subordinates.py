#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
from asyncio import gather
from base64 import b64encode

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_SERIES,
    get_unit_address,
    scale_application,
)

DATABASE_APP_NAME = "pg"
LS_CLIENT = "landscape-client"

logger = logging.getLogger(__name__)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm: str, github_secrets):
    landscape_config = {
        "admin_email": "admin@example.com",
        "admin_name": "Admin",
        "admin_password": "qweqwepoipoi",
    }
    await gather(
        ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=3,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        ),
        ops_test.model.deploy("landscape-scalable"),
        ops_test.model.deploy(LS_CLIENT, num_units=0),
    )
    await ops_test.model.applications["landscape-server"].set_config(landscape_config)

    await ops_test.model.wait_for_idle(
        apps=["landscape-server", "haproxy", DATABASE_APP_NAME], status="active", timeout=3000
    )
    haproxy_unit = ops_test.model.applications["haproxy"].units[0]
    haproxy_addr = get_unit_address(ops_test, haproxy_unit.name)
    haproxy_host = haproxy_unit.machine.hostname
    cert = subprocess.check_output([
        "lxc",
        "exec",
        haproxy_host,
        "cat",
        "/var/lib/haproxy/selfsigned_ca.crt",
    ])
    ssl_public_key = f"base64:{b64encode(cert).decode()}"

    await ops_test.model.applications[LS_CLIENT].set_config({
        "account-name": "standalone",
        "ping-url": f"http://{haproxy_addr}/ping",
        "url": f"https://{haproxy_addr}/message-system",
        "ssl-public-key": ssl_public_key,
    })
    await ops_test.model.relate(f"{DATABASE_APP_NAME}:juju-info", f"{LS_CLIENT}:container")
    await ops_test.model.wait_for_idle(apps=[LS_CLIENT, DATABASE_APP_NAME], status="active")


@pytest.mark.group(1)
async def test_scale_up(ops_test: OpsTest, github_secrets):
    await scale_application(ops_test, DATABASE_APP_NAME, 4)

    await ops_test.model.wait_for_idle(
        apps=[LS_CLIENT, DATABASE_APP_NAME], status="active", timeout=1500
    )


@pytest.mark.group(1)
async def test_scale_down(ops_test: OpsTest, github_secrets):
    await scale_application(ops_test, DATABASE_APP_NAME, 3)

    await ops_test.model.wait_for_idle(
        apps=[LS_CLIENT, DATABASE_APP_NAME], status="active", timeout=1500
    )
