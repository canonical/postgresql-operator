# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
from unittest.mock import mock_open, patch

from utils import new_password, render_file


def test_new_password():
    # Test the password generation twice in order to check if we get different passwords and
    # that they meet the required criteria.
    first_password = new_password()
    assert len(first_password) == 16
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password) is not None

    second_password = new_password()
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password) is not None
    assert second_password != first_password


def test_render_file():
    with (
        patch("os.chmod") as _chmod,
        patch("os.chown") as _chown,
        patch("pwd.getpwnam") as _pwnam,
        patch("tempfile.NamedTemporaryFile") as _temp_file,
    ):
        # Set a mocked temporary filename.
        filename = "/tmp/temporaryfilename"
        _temp_file.return_value.name = filename
        # Setup a mock for the `open` method.
        mock = mock_open()
        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Set the uid/gid return values for lookup of 'postgres' user.
            _pwnam.return_value.pw_uid = 35
            _pwnam.return_value.pw_gid = 35
            # Call the method using a temporary configuration file.
            render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        assert mock.call_args_list[0][0] == (filename, "w+")
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("_daemon_")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

        # Test when it's requested to not change the file owner.
        mock.reset_mock()
        _pwnam.reset_mock()
        _chmod.reset_mock()
        _chown.reset_mock()
        with patch("builtins.open", mock, create=True):
            render_file(filename, "rendered-content", 0o640, change_owner=False)
        _pwnam.assert_not_called()
        _chmod.assert_called_once_with(filename, 0o640)
        _chown.assert_not_called()
