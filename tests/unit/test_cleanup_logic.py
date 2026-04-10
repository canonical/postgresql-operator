# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import MagicMock, patch

from ops.testing import Harness

from charm import PostgresqlOperatorCharm


class TestCleanupLogic(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm

    def test_remove_lost_and_found_logic(self):
        """Test the internal logic of _remove_lost_and_found."""
        # Test successful removal
        with (
            patch("charm.STORAGE_PATHS", ["/data/storage1"]),
            patch("charm.Path") as mock_path_class,
            patch("charm.shutil.rmtree") as mock_rmtree,
            patch("charm.logger") as mock_logger,
        ):
            mock_path_instance = MagicMock()
            mock_target = MagicMock()

            mock_path_class.return_value = mock_path_instance
            mock_path_instance.__truediv__.return_value = mock_target
            mock_target.is_dir.return_value = True
            mock_target.__str__.return_value = "/data/storage1/lost+found"

            self.charm._remove_lost_and_found()

            mock_rmtree.assert_called_once_with(mock_target)
            mock_logger.info.assert_called_with("Removing /data/storage1/lost+found")

    def test_remove_lost_and_found_error_handling(self):
        """Test the error handling of _remove_lost_and_found."""
        with (
            patch("charm.STORAGE_PATHS", ["/data/storage1"]),
            patch("charm.Path") as mock_path_class,
            patch("charm.shutil.rmtree", side_effect=OSError("Permission denied")),
            patch("charm.logger") as mock_logger,
        ):
            mock_path_instance = MagicMock()
            mock_target = MagicMock()
            mock_path_class.return_value = mock_path_instance
            mock_path_instance.__truediv__.return_value = mock_target
            mock_target.is_dir.return_value = True
            mock_target.__str__.return_value = "/data/storage1/lost+found"

            self.charm._remove_lost_and_found()
            mock_logger.exception.assert_called_with("Failed to remove /data/storage1/lost+found")
