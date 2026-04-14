#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from asyncio import gather

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE,
    scale_application,
)

DATABASE_APP_NAME = "pg"
LS_CLIENT = "landscape-client"
UBUNTU_PRO_APP_NAME = "ubuntu-advantage"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
async def check_subordinate_env_vars(ops_test: OpsTest) -> None:
    if (
        not os.environ.get("UBUNTU_PRO_TOKEN", "").strip()
        or not os.environ.get("LANDSCAPE_ACCOUNT_NAME", "").strip()
        or not os.environ.get("LANDSCAPE_REGISTRATION_KEY", "").strip()
    ):
        pytest.skip("Subordinate configs not set")


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm: str, check_subordinate_env_vars):
    await gather(
        ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=3,
            base=CHARM_BASE,
        ),
        ops_test.model.deploy(
            UBUNTU_PRO_APP_NAME,
            config={"token": os.environ["UBUNTU_PRO_TOKEN"]},
            channel="latest/edge",
            num_units=0,
            base=CHARM_BASE,
            # TODO switch back to series when pylib juju can figure out the base:
            # https://github.com/juju/python-libjuju/issues/1240
            series="jammy",
        ),
        ops_test.model.deploy(
            LS_CLIENT,
            config={
                "account-name": os.environ["LANDSCAPE_ACCOUNT_NAME"],
                "registration-key": os.environ["LANDSCAPE_REGISTRATION_KEY"],
                "ppa": "ppa:landscape/self-hosted-beta",
            },
            channel="latest/edge",
            num_units=0,
            base=CHARM_BASE,
        ),
    )

    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=2000)
    await ops_test.model.relate(f"{DATABASE_APP_NAME}:juju-info", f"{LS_CLIENT}:container")
    await ops_test.model.relate(
        f"{DATABASE_APP_NAME}:juju-info", f"{UBUNTU_PRO_APP_NAME}:juju-info"
    )
    await ops_test.model.wait_for_idle(
        apps=[LS_CLIENT, UBUNTU_PRO_APP_NAME, DATABASE_APP_NAME], status="active"
    )


async def test_scale_up(ops_test: OpsTest, check_subordinate_env_vars):
    await scale_application(ops_test, DATABASE_APP_NAME, 4)

    await ops_test.model.wait_for_idle(
        apps=[LS_CLIENT, UBUNTU_PRO_APP_NAME, DATABASE_APP_NAME], status="active", timeout=1500
    )


async def test_scale_down(ops_test: OpsTest, check_subordinate_env_vars):
    await scale_application(ops_test, DATABASE_APP_NAME, 3)

    await ops_test.model.wait_for_idle(
        apps=[LS_CLIENT, UBUNTU_PRO_APP_NAME, DATABASE_APP_NAME], status="active", timeout=1500
    )
