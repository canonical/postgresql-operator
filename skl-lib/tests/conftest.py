# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import Literal

import pytest

# To separate vm and k8s tests
type Substrate = Literal["vm", "k8s"]


# This causes every test that uses the `substrate` fixture to run twice,
# once with substrate="vm" and once with substrate="k8s".
@pytest.fixture(params=["vm", "k8s"], autouse=True, scope="session")
def substrate(request) -> Substrate:
    """The substrate that we are testing."""
    return request.param


@pytest.fixture(scope="session")
def test_charm_path(substrate) -> str:
    """The path to test charm based on substrate."""
    return f"tests/charms/postgresql_{substrate}_test_charm"
