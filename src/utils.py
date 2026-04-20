# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions that are used in the charm."""

import os
import pwd
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


def render_file(path: str, content: str, mode: int, change_owner: bool = True) -> None:
    """Write a content rendered from a template to a file.

    Args:
        path: the path to the file.
        content: the data to be written to the file.
        mode: access permission mask applied to the
          file using chmod (e.g. 0o640).
        change_owner: whether to change the file owner
          to the _daemon_ user.
    """
    # TODO: keep this method to use it also for generating replication configuration files and
    # move it to an utils / helpers file.
    # Write the content to the file.
    with open(path, "w+") as file:
        file.write(content)
    # Ensure correct permissions are set on the file.
    os.chmod(path, mode)
    if change_owner:
        _change_owner(path)


def create_directory(path: str, mode: int) -> None:
    """Creates a directory.

    Args:
        path: the path of the directory that should be created.
        mode: access permission mask applied to the
          directory using chmod (e.g. 0o640).
    """
    os.makedirs(path, mode=mode, exist_ok=True)
    # Ensure correct permissions are set on the directory.
    os.chmod(path, mode)
    _change_owner(path)


def _change_owner(path: str) -> None:
    """Change the ownership of a file or a directory to the postgres user.

    Args:
        path: path to a file or directory.
    """
    # Get the uid/gid for the _daemon_ user.
    user_database = pwd.getpwnam("_daemon_")
    # Set the correct ownership for the file or directory.
    os.chown(path, uid=user_database.pw_uid, gid=user_database.pw_gid)
