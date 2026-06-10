# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL enums."""

from enum import Enum


class Substrates(str, Enum):
    """Possible substrates."""

    K8S = "k8s"
    VM = "vm"
