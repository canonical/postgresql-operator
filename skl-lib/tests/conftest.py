# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import Literal

import pytest
from _pytest.config.argparsing import Parser

# To separate vm and k8s tests
type Substrate = Literal["vm", "k8s"]


def pytest_addoption(parser: Parser):
    parser.addoption(
        "--substrate",
        action="store",
        help="Substrate to test, either vm or k8s",
        choices=("vm", "k8s"),
        default="vm",
    )


@pytest.fixture(scope="session")
def substrate(request) -> Substrate:
    """The substrate that we are testing."""
    return request.config.option.substrate


@pytest.fixture(scope="session")
def test_charm_path(substrate) -> str:
    """The path to test charm based on substrate."""
    return f"tests/charms/postgresql_{substrate}_test_charm"
