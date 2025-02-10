# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest

from . import architecture


@pytest.fixture(scope="session")
def charm():
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./postgresql_ubuntu@22.04-{architecture.architecture}.charm"
