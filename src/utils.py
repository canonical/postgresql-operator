# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions that are used in the charm."""

import platform
import secrets
import string

from constants import (
    POSTGRESQL_SNAP_NAME,
    SNAP_PACKAGES,
)


def new_password() -> str:
    """Generate a random password string.

    Returns:
       A random password string.
    """
    choices = string.ascii_letters + string.digits
    password = "".join([secrets.choice(choices) for i in range(16)])
    return password


def snap_refreshed(target_rev: str) -> bool:
    """Whether the snap was refreshed to the target version."""
    arch = platform.machine()

    for snap_package in SNAP_PACKAGES:
        snap_name = snap_package[0]
        snap_revs = snap_package[1]["revision"]
        if snap_name == POSTGRESQL_SNAP_NAME and target_rev != snap_revs.get(arch):
            return False

    return True


def label2name(label: str) -> str:
    """Convert a unit label (with `-`) to a unit name (with `/`).

    Args:
        label: The label to convert.

    Returns:
        The converted name.
    """
    return label.rsplit("-", 1)[0] + "/" + label.rsplit("-", 1)[1]
