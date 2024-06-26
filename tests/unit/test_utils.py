# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re

from utils import new_password


def test_new_password():
    # Test the password generation twice in order to check if we get different passwords and
    # that they meet the required criteria.
    first_password = new_password()
    assert len(first_password) == 16
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password) is not None

    second_password = new_password()
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password) is not None
    assert second_password != first_password
