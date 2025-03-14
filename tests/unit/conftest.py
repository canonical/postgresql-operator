#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import PropertyMock

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled


# This causes every test defined in this file to run 2 times, each with
# charm.JujuVersion.has_secrets set as True or as False
@pytest.fixture(autouse=True)
def _has_secrets(request, monkeypatch):
    monkeypatch.setattr("charm.JujuVersion.has_secrets", PropertyMock(return_value=True))


@pytest.fixture(autouse=True)
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield
