# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, mock_open, patch

import pytest
from single_kernel_postgresql.config.literals import SNAP_USER
from single_kernel_postgresql.utils.filesystem import change_owner, is_tmpfs


def test_change_owner_calls_pwd_and_os_chown_with_daemon_user():
    with (
        patch("single_kernel_postgresql.utils.filesystem.pwd.getpwnam") as getpwnam,
        patch("single_kernel_postgresql.utils.filesystem.os.chown") as chown,
        NamedTemporaryFile(delete=True) as tmp,
    ):
        # Simulate pwd entry
        pw_entry = MagicMock()
        pw_entry.pw_uid = 1234
        pw_entry.pw_gid = 4321
        getpwnam.return_value = pw_entry

        change_owner(tmp.name)

        # Ensure getpwnam was called for SNAP_USER and ended up using snap user
        getpwnam.assert_called_once_with(SNAP_USER)
        chown.assert_called_once_with(tmp.name, uid=1234, gid=4321)


def test_change_owner_raises_when_user_missing():
    # When the _daemon_ user is not present, pwd.getpwnam raises KeyError
    with (
        patch("single_kernel_postgresql.utils.filesystem.pwd.getpwnam", side_effect=KeyError),
        pytest.raises(KeyError),
        NamedTemporaryFile(delete=True) as tmp,
    ):
        change_owner(tmp.name)


def test_change_owner_bubbles_up_os_error():
    # Ensure we surface OSError coming from os.chown
    with (
        patch("single_kernel_postgresql.utils.filesystem.pwd.getpwnam") as getpwnam,
        patch("single_kernel_postgresql.utils.filesystem.os.chown", side_effect=OSError("denied")),
        NamedTemporaryFile(delete=True) as tmp,
    ):
        entry = MagicMock()
        entry.pw_uid = 1
        entry.pw_gid = 1
        getpwnam.return_value = entry
        with pytest.raises(OSError):
            change_owner(tmp.name)


def test_is_tmpfs_detects_tmpfs_correctly():
    """Test that is_tmpfs correctly identifies tmpfs filesystems."""
    proc_mounts_content = (
        "tmpfs /tmp tmpfs rw,nosuid,nodev 0 0\n"
        "tmpfs /run tmpfs rw,nosuid,nodev,noexec,relatime 0 0\n"
        "/dev/sda1 / ext4 rw,relatime 0 0\n"
        "/dev/sda1 /var ext4 rw,relatime 0 0\n"
    )
    with patch("builtins.open", mock_open(read_data=proc_mounts_content)):
        assert is_tmpfs("/tmp/test") is True
        assert is_tmpfs("/run/postgresql") is True
        assert is_tmpfs("/var/lib/postgresql") is False
        assert is_tmpfs("/") is False


def test_is_tmpfs_handles_nested_paths():
    """Test that is_tmpfs correctly handles nested paths under mount points."""
    proc_mounts_content = (
        "tmpfs /dev/shm tmpfs rw,nosuid,nodev 0 0\n/dev/sda1 / ext4 rw,relatime 0 0\n"
    )
    with patch("builtins.open", mock_open(read_data=proc_mounts_content)):
        assert is_tmpfs("/dev/shm") is True
        assert is_tmpfs("/dev/shm/postgresql") is True
        assert is_tmpfs("/dev/shm/postgresql/temp") is True
        assert is_tmpfs("/dev/other") is False


def test_is_tmpfs_uses_longest_mount_match():
    """Test that is_tmpfs uses the most specific (longest) mount point."""
    proc_mounts_content = (
        "/dev/sda1 / ext4 rw,relatime 0 0\n"
        "tmpfs /var/tmp tmpfs rw,nosuid,nodev 0 0\n"
        "/dev/sdb1 /var ext4 rw,relatime 0 0\n"
    )
    with patch("builtins.open", mock_open(read_data=proc_mounts_content)):
        # /var is ext4, but /var/tmp is tmpfs - should match the longer path
        assert is_tmpfs("/var/tmp/test") is True
        assert is_tmpfs("/var/lib/test") is False


def test_is_tmpfs_handles_relative_paths():
    """Test that is_tmpfs correctly resolves relative paths."""
    proc_mounts_content = "tmpfs /run tmpfs rw,nosuid,nodev 0 0\n"
    with (
        patch("builtins.open", mock_open(read_data=proc_mounts_content)),
        patch("single_kernel_postgresql.utils.filesystem.os.path.abspath") as mock_abspath,
    ):
        mock_abspath.return_value = "/run/test"
        assert is_tmpfs("../run/test") is True
        mock_abspath.assert_called_once_with("../run/test")


def test_is_tmpfs_handles_missing_proc_mounts():
    """Test that is_tmpfs returns False when /proc/mounts is unavailable."""
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert is_tmpfs("/any/path") is False


def test_is_tmpfs_handles_permission_error():
    """Test that is_tmpfs returns False when /proc/mounts cannot be read."""
    with patch("builtins.open", side_effect=PermissionError):
        assert is_tmpfs("/any/path") is False


def test_is_tmpfs_handles_malformed_mount_lines():
    """Test that is_tmpfs gracefully handles malformed lines in /proc/mounts."""
    proc_mounts_content = (
        "invalid line\n"
        "device /path\n"  # Only 2 parts
        "tmpfs /tmp tmpfs rw,nosuid,nodev 0 0\n"  # Valid line
        "\n"  # Empty line
    )
    with patch("builtins.open", mock_open(read_data=proc_mounts_content)):
        # Should still work and find the valid tmpfs line
        assert is_tmpfs("/tmp/test") is True
