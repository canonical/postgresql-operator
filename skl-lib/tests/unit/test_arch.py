# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock
from unittest.mock import patch

from single_kernel_postgresql.utils.arch import is_wrong_architecture


def test_wrong_architecture_file_not_found():
    """Returns False when the manifest file does not exist."""
    with (
        patch("single_kernel_postgresql.utils.arch.os.environ.get", return_value="/tmp"),
        patch("single_kernel_postgresql.utils.arch.os.path.exists", return_value=False),
    ):
        assert not is_wrong_architecture()


def test_wrong_architecture_amd64():
    """Correctly identifies architecture when the charm is AMD."""
    with (
        patch("single_kernel_postgresql.utils.arch.os.environ.get", return_value="/tmp"),
        patch("single_kernel_postgresql.utils.arch.os.path.exists", return_value=True),
        patch("builtins.open", mock.mock_open(read_data="amd64\n")),
        patch("single_kernel_postgresql.utils.arch.os.uname") as _uname,
    ):
        _uname.return_value = mock.Mock(machine="x86_64")
        assert not is_wrong_architecture()
        _uname.return_value = mock.Mock(machine="aarch64")
        assert is_wrong_architecture()


def test_wrong_architecture_arm64():
    """Correctly identifies architecture when the charm is ARM."""
    with (
        patch("single_kernel_postgresql.utils.arch.os.environ.get", return_value="/tmp"),
        patch("single_kernel_postgresql.utils.arch.os.path.exists", return_value=True),
        patch("builtins.open", mock.mock_open(read_data="arm64\n")),
        patch("single_kernel_postgresql.utils.arch.os.uname") as _uname,
    ):
        _uname.return_value = mock.Mock(machine="x86_64")
        assert is_wrong_architecture()
        _uname.return_value = mock.Mock(machine="aarch64")
        assert not is_wrong_architecture()
