# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import get_args

from single_kernel_postgresql.config.locales import LOCALES


def test_locales_includes_k8s_extras():
    members = get_args(LOCALES)
    assert "C" in members
    assert "C.utf8" in members
    assert "POSIX" in members
