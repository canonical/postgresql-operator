# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
from single_kernel_postgresql.core.config import CharmConfig


def test_charmconfig_imports_and_keys_resolve():
    keys = CharmConfig.keys()
    assert "response_lc_time" in keys
    assert "response_lc_monetary" in keys
    assert "response_lc_numeric" in keys
