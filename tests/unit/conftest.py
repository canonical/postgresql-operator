#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from unittest.mock import PropertyMock

# This causes every test defined in this file to run 2 times, each with
# charm.JujuVersion.has_secrets set as True or as False
@pytest.fixture(params=[True, False], autouse=True)
def _has_secrets(request, monkeypatch):
    monkeypatch.setattr("charm.JujuVersion.has_secrets", PropertyMock(return_value=request.param))
    return request.param
