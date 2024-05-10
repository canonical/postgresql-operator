# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest

from . import architecture
from .juju_ import juju_major_version

juju2 = pytest.mark.skipif(juju_major_version != 2, reason="Requires juju 2")
juju3 = pytest.mark.skipif(juju_major_version != 3, reason="Requires juju 3")
juju_secrets = pytest.mark.skipif(juju_major_version < 3, reason="Requires juju secrets")
amd64_only = pytest.mark.skipif(
    architecture.architecture != "amd64", reason="Requires amd64 architecture"
)
arm64_only = pytest.mark.skipif(
    architecture.architecture != "arm64", reason="Requires arm64 architecture"
)
