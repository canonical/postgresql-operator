#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import pathlib
import platform
from unittest.mock import PropertyMock

import pytest
import tomli
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled


# This causes every test defined in this file to run 2 times, each with
# ops.JujuVersion.has_secrets set as True or as False
@pytest.fixture(autouse=True)
def _has_secrets(request, monkeypatch):
    monkeypatch.setattr("ops.JujuVersion.has_secrets", PropertyMock(return_value=True))


@pytest.fixture(autouse=True)
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield


class _MockRefresh:
    in_progress = False
    next_unit_allowed_to_refresh = True
    workload_allowed_to_start = True
    app_status_higher_priority = None
    unit_status_higher_priority = None

    def __init__(self, _, /):
        pass

    def update_snap_revision(self):
        pass

    @property
    def pinned_snap_revision(self):
        with pathlib.Path("refresh_versions.toml").open("rb") as file:
            return tomli.load(file)["snap"]["revisions"][platform.machine()]

    def unit_status_lower_priority(self, *, workload_is_running=True):
        return None


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr("charm_refresh.Machines", _MockRefresh)
