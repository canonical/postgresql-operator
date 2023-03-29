# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions that are used in the charm."""
import secrets
import string
from hashlib import md5


def new_md5_hashed_password(username: str, password: str) -> str:
    """Generate an MD5 hashed password string.

    Returns:
       An MD5 hashed password string.
    """
    hash_password = md5((password + username).encode()).hexdigest()
    return f"md5{hash_password}"


def new_password() -> str:
    """Generate a random password string.

    Returns:
       A random password string.
    """
    choices = string.ascii_letters + string.digits
    password = "".join([secrets.choice(choices) for i in range(16)])
    return password
