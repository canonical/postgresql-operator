# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import get_args

from single_kernel_postgresql.config.locales import LOCALES
from single_kernel_postgresql.core.config import CharmConfig


def test_locales_includes_k8s_extras():
    """The shared enum is the 512-superset: VM's 510 plus the K8s extras."""
    members = get_args(LOCALES)
    assert "C" in members
    assert "C.utf8" in members
    assert "POSIX" in members


def test_charmconfig_imports_and_keys_resolve():
    """CharmConfig still imports after the rename and exposes its fields."""
    keys = CharmConfig.keys()
    assert "response_lc_time" in keys
    assert "response_lc_monetary" in keys
    assert "response_lc_numeric" in keys
