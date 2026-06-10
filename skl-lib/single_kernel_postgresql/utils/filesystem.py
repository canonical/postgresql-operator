# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Filesystem utilities."""

import os
import pwd

from ..config.literals import SNAP_USER


def change_owner(path: str) -> None:
    """Change the ownership of a file or a directory to the snap user.

    Args:
        path: path to a file or directory.
    """
    # Get the uid/gid for the snap user.
    user_database = pwd.getpwnam(SNAP_USER)
    # Set the correct ownership for the file or directory.
    os.chown(path, uid=user_database.pw_uid, gid=user_database.pw_gid)


def is_tmpfs(path: str) -> bool:
    """Check if a path is on a tmpfs filesystem.

    This function reads /proc/mounts to determine the filesystem type of the
    mount point containing the given path.

    Args:
        path: Path to check.

    Returns:
        True if the path is on a tmpfs filesystem, False otherwise.
        Returns False if /proc/mounts cannot be read (e.g., not on Linux).
    """
    try:
        with open("/proc/mounts") as f:
            mounts = f.readlines()
    except (FileNotFoundError, PermissionError):
        # Not on Linux or /proc not available, assume persistent storage
        return False

    # Get absolute path to handle relative paths and symlinks
    abs_path = os.path.abspath(path)

    # Find the longest matching mount point (most specific)
    best_match_fs = None
    best_match_len = 0

    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point = parts[1]
        fs_type = parts[2]

        # Check if path is under this mount point and is more specific
        if abs_path.startswith(mount_point) and len(mount_point) > best_match_len:
            best_match_fs = fs_type
            best_match_len = len(mount_point)

    return best_match_fs == "tmpfs"
