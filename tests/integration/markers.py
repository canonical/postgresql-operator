# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest

from .juju_ import juju_major_version

juju2 = pytest.mark.skipif(juju_major_version != 2, reason="Requires juju 2")
juju3 = pytest.mark.skipif(juju_major_version != 3, reason="Requires juju 3")
juju_secrets = pytest.mark.skipif(juju_major_version < 3, reason="Requires juju secrets")
