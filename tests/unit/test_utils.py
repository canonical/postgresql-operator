# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
from unittest.mock import patch

from constants import POSTGRESQL_SNAP_NAME
from utils import new_password, snap_refreshed


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
