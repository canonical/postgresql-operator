# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
from unittest.mock import Mock, patch, sentinel

from constants import POSTGRESQL_SNAP_NAME
from utils import _remove_stale_otel_sdk_packages, new_password, snap_refreshed


def test_new_password():
    # Test the password generation twice in order to check if we get different passwords and
    # that they meet the required criteria.
    first_password = new_password()
    assert len(first_password) == 16
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password) is not None

    second_password = new_password()
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password) is not None
    assert second_password != first_password


def test_snap_refreshed():
    with patch(
        "utils.SNAP_PACKAGES",
        [(POSTGRESQL_SNAP_NAME, {"revision": {"aarch64": "100", "x86_64": "100"}})],
    ):
        assert snap_refreshed("100") is True
        assert snap_refreshed("200") is False

    with patch(
        "utils.SNAP_PACKAGES",
        [(POSTGRESQL_SNAP_NAME, {"revision": {}})],
    ):
        assert snap_refreshed("100") is False
        assert snap_refreshed("200") is False


def test_remove_stale_otel_sdk_packages():
    with (
        patch("utils.os.getenv", return_value=None) as _getenv,
        patch("utils.shutil") as _shutil,
        patch("utils.distributions") as _distributions,
    ):
        other_dist = Mock()
        other_dist._normalized_name = "test"
        otel_dist = Mock()
        otel_dist._normalized_name = "opentelemetry_test"
        stale_otel_dist = Mock()
        stale_otel_dist._normalized_name = "opentelemetry_test"
        stale_otel_dist.files = []
        stale_otel_dist._path = sentinel.path

        # Not called if not upgrade hook
        _remove_stale_otel_sdk_packages()

        _distributions.assert_not_called()
        _shutil.rmtree.assert_not_called()
        _distributions.reset_mock()
        _shutil.rmtree.reset_mock()

        # don't execute on Juju 3
        _getenv.side_effect = ["3.0.0", "hooks/upgrade-charm"]
        _remove_stale_otel_sdk_packages()

        _distributions.assert_not_called()
        _shutil.rmtree.assert_not_called()
        _distributions.reset_mock()
        _shutil.rmtree.reset_mock()

        # Upgrade hook, nothing to remove
        _getenv.side_effect = ["2.9.53", "hooks/upgrade-charm"]
        _distributions.return_value = [other_dist, otel_dist]

        _remove_stale_otel_sdk_packages()

        _distributions.assert_called_once_with()
        _shutil.rmtree.assert_not_called()
        _distributions.reset_mock()
        _shutil.rmtree.reset_mock()

        # Upgrade hook, duplicate otel packages
        _getenv.side_effect = ["2.9.53", "hooks/upgrade-charm"]
        _distributions.return_value = [other_dist, otel_dist, stale_otel_dist]
        _remove_stale_otel_sdk_packages()

        _distributions.assert_called_once_with()
        _shutil.rmtree.assert_called_once_with(sentinel.path)
        _distributions.reset_mock()
        _shutil.rmtree.reset_mock()
