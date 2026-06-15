#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import os
import pathlib

from . import markers
from .adapters import JujuFixture
from .jubilant_helpers import DATABASE_APP_NAME


def fetch_charm(charm_path: str | os.PathLike, architecture: str) -> pathlib.Path:
    """Fetch a packed charm for a specific architecture, ignoring the host architecture."""
    charm_path = pathlib.Path(charm_path)
    packed_charms = list(charm_path.glob(f"*-{architecture}.charm"))
    return packed_charms[0].resolve(strict=True)


@markers.amd64_only
def test_arm_charm_on_amd_host(juju: JujuFixture) -> None:
    """Try deploying an arm64 charm on an amd64 host."""
    charm = fetch_charm(".", "arm64")
    juju.ext.model.deploy(
        str(charm),
        application_name=DATABASE_APP_NAME,
        num_units=1,
        config={"profile": "testing"},
    )
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], raise_on_error=False, status="blocked")


@markers.arm64_only
def test_amd_charm_on_arm_host(juju: JujuFixture) -> None:
    """Try deploying an amd64 charm on an arm64 host."""
    charm = fetch_charm(".", "amd64")
    juju.ext.model.deploy(
        str(charm),
        application_name=DATABASE_APP_NAME,
        num_units=1,
        config={"profile": "testing"},
    )
    juju.ext.model.wait_for_idle(apps=[DATABASE_APP_NAME], raise_on_error=False, status="blocked")
