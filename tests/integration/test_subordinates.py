#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os

import pytest

from .adapters import JujuFixture
from .jubilant_helpers import (
    scale_application,
)

DATABASE_APP_NAME = "pg"
LS_CLIENT = "landscape-client"
UBUNTU_PRO_APP_NAME = "ubuntu-advantage"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def check_subordinate_env_vars(juju: JujuFixture) -> None:
    if (
        not os.environ.get("UBUNTU_PRO_TOKEN", "").strip()
        or not os.environ.get("LANDSCAPE_ACCOUNT_NAME", "").strip()
        or not os.environ.get("LANDSCAPE_REGISTRATION_KEY", "").strip()
    ):
        pytest.skip("Subordinate configs not set")


@pytest.mark.abort_on_fail
def test_deploy(juju: JujuFixture, charm: str, check_subordinate_env_vars):
    juju.ext.model.deploy(
        charm,
        application_name=DATABASE_APP_NAME,
        num_units=3,
    )
    juju.ext.model.deploy(
        UBUNTU_PRO_APP_NAME,
        config={"token": os.environ["UBUNTU_PRO_TOKEN"]},
        channel="latest/edge",
        num_units=0,
        # TODO switch back to series when pylib juju can figure out the base:
        # https://github.com/juju/python-libjuju/issues/1240
        series="noble",
    )
    juju.ext.model.deploy(
        LS_CLIENT,
        config={
            "account-name": os.environ["LANDSCAPE_ACCOUNT_NAME"],
            "registration-key": os.environ["LANDSCAPE_REGISTRATION_KEY"],
            "ppa": "ppa:landscape/self-hosted-beta",
        },
        channel="latest/edge",
        num_units=0,
    )

    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=2000)
    juju.ext.model.relate(f"{DATABASE_APP_NAME}:juju-info", f"{LS_CLIENT}:container")
    juju.ext.model.relate(
        f"{DATABASE_APP_NAME}:juju-info", f"{UBUNTU_PRO_APP_NAME}:juju-info"
    )
    juju.ext.model.wait_for_idle(
        apps=[LS_CLIENT, UBUNTU_PRO_APP_NAME, DATABASE_APP_NAME], status="active"
    )


def test_scale_up(juju: JujuFixture, check_subordinate_env_vars):
    scale_application(juju, DATABASE_APP_NAME, 4)

    juju.ext.model.wait_for_idle(
        apps=[LS_CLIENT, UBUNTU_PRO_APP_NAME, DATABASE_APP_NAME], status="active", timeout=1500
    )


def test_scale_down(juju: JujuFixture, check_subordinate_env_vars):
    scale_application(juju, DATABASE_APP_NAME, 3)

    juju.ext.model.wait_for_idle(
        apps=[LS_CLIENT, UBUNTU_PRO_APP_NAME, DATABASE_APP_NAME], status="active", timeout=1500
    )
