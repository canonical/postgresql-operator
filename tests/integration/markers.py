# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest
from juju.model import Model

juju2 = pytest.mark.skipif(not hasattr(Model, "list_secrets"), reason="Requires juju 2")
juju3 = pytest.mark.skipif(hasattr(Model, "list_secrets"), reason="Requires juju 3")
