# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
import unittest

from utils import new_password


class TestUtils(unittest.TestCase):
    def test_new_password(self):
        # Test the password generation twice in order to check if we get different passwords and
        # that they meet the required criteria.
        first_password = new_password()
        self.assertEqual(len(first_password), 16)
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password))

        second_password = new_password()
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password))
        self.assertNotEqual(second_password, first_password)
