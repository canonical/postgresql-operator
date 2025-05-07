# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions that are used in the charm."""

import secrets
import string


def new_password() -> str:
    """Generate a random password string.

    Returns:
       A random password string.
    """
    choices = string.ascii_letters + string.digits
    password = "".join([secrets.choice(choices) for i in range(16)])
    return password


def label2name(label: str) -> str:
    """Convert a unit label (with `-`) to a unit name (with `/`).

    Args:
        label: The label to convert.

    Returns:
        The converted name.
    """
    return label.rsplit("-", 1)[0] + "/" + label.rsplit("-", 1)[1]
