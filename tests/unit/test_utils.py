# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
from unittest import TestCase

from utils import new_password

# used for assert functions
tc = TestCase()


def test_new_password():
    # Test the password generation twice in order to check if we get different passwords and
    # that they meet the required criteria.
    first_password = new_password()
    tc.assertEqual(len(first_password), 16)
    tc.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password))

    second_password = new_password()
    tc.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password))
    tc.assertNotEqual(second_password, first_password)
