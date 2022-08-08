# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, PropertyMock, patch

from charm import PostgresqlOperatorCharm
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from constants import DATABASE_PORT, PEER
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from tests.helpers import patch_network_get

DATABASE = "test_database"
EXTRA_USER_ROLES = "CREATEDB,CREATEROLE"
RELATION_NAME = "db"
POSTGRESQL_VERSION = "12"


@patch_network_get(private_address="1.1.1.1")
class TestDbProvides(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.harness.set_leader(True)
        self.harness.begin()
        self.app = self.harness.charm.app.name
        self.unit = self.harness.charm.unit.name

        # Define some relations.
        self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
        self.harness.add_relation_unit(self.rel_id, "application/0")
        self.harness.add_relation_unit(self.rel_id, self.unit)
        self.peer_rel_id = self.harness.add_relation(PEER, self.app)
        self.harness.add_relation_unit(self.peer_rel_id, self.unit)
        self.harness.update_relation_data(
            self.peer_rel_id,
            self.app,
            {"cluster_initialised": "True"},
        )
        # self.provider = self.harness.charm.postgresql_client_relation

    def request_database(self):
        # Reset the charm status.
        self.harness.model.unit.status = ActiveStatus()

        with self.harness.hooks_disabled():
            # Reset the application databag.
            self.harness.update_relation_data(
                self.rel_id,
                "application/0",
                {"database": ""},
            )

            # Reset the database databag.
            self.harness.update_relation_data(
                self.rel_id,
                self.app,
                {
                    "allowed-subnets": "",
                    "allowed-units": "",
                    "port": "",
                    "version": "",
                    "user": "",
                    "password": "",
                    "database": "",
                },
            )

        # Simulate the request of a new database.
        self.harness.update_relation_data(
            self.rel_id,
            "application/0",
            {"database": DATABASE},
        )

    @patch("charm.DbProvides.update_endpoints")
    @patch("relations.db.new_password", return_value="test-password")
    @patch.object(EventBase, "defer")
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    def test_on_relation_changed(
        self, _member_started, _primary_endpoint, _defer, _new_password, _update_endpoints
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _member_started.side_effect = [False, True, True, True, True, True]
            _primary_endpoint.side_effect = [
                None,
                {"1.1.1.1"},
                {"1.1.1.1"},
                {"1.1.1.1"},
                {"1.1.1.1"},
            ]
            postgresql_mock.create_user = PropertyMock(
                side_effect=[None, PostgreSQLCreateUserError, None, None]
            )
            postgresql_mock.create_database = PropertyMock(
                side_effect=[None, PostgreSQLCreateDatabaseError, None]
            )
            postgresql_mock.get_postgresql_version = PropertyMock(
                side_effect=[
                    POSTGRESQL_VERSION,
                    PostgreSQLGetPostgreSQLVersionError,
                ]
            )

            # Request a database before the database is ready.
            self.request_database()
            _defer.assert_called_once()

            # Request a database before primary endpoint is available.
            self.request_database()
            self.assertEqual(_defer.call_count, 2)

            # Request it again when the database is ready.
            self.request_database()

            # Assert that the correct calls were made.
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.create_user.assert_called_once_with(user, "test-password", False)
            postgresql_mock.create_database.assert_called_once_with(DATABASE, user)
            postgresql_mock.get_postgresql_version.assert_called_once()
            _update_endpoints.assert_called_once()

            # Assert that the relation data was updated correctly.
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.app),
                {
                    "allowed-units": "application/0",
                    "database": DATABASE,
                    "password": "test-password",
                    "port": DATABASE_PORT,
                    "user": user,
                    "version": POSTGRESQL_VERSION,
                },
            )

            # Assert no BlockedStatus was set.
            self.assertFalse(isinstance(self.harness.model.unit.status, BlockedStatus))

            # BlockedStatus due to a PostgreSQLCreateUserError.
            self.request_database()
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))
            # No data is set in the databag by the database.
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.app),
                {},
            )

            # BlockedStatus due to a PostgreSQLCreateDatabaseError.
            self.request_database()
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))
            # No data is set in the databag by the database.
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.app),
                {},
            )

            # BlockedStatus due to a PostgreSQLGetPostgreSQLVersionError.
            self.request_database()
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch.object(EventBase, "defer")
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken(
        self, _on_relation_departed, _member_started, _primary_endpoint, _defer
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _member_started.side_effect = [False, True, True, True]
            _primary_endpoint.side_effect = [None, {"1.1.1.1"}, {"1.1.1.1"}]
            postgresql_mock.delete_user = PropertyMock(
                side_effect=[None, PostgreSQLDeleteUserError]
            )

            # Break the relation before the database is ready.
            _defer.assert_not_called()
            self.harness.remove_relation(self.rel_id)
            _defer.assert_called_once()

            # Break the relation before primary endpoint is available.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            self.assertEqual(_defer.call_count, 2)

            # Assert that the correct calls were made after a relation broken event.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.delete_user.assert_called_once_with(user)

            # Test a failed user deletion.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    # @patch(
    #     "charm.PostgresqlOperatorCharm.primary_endpoint",
    #     new_callable=PropertyMock(return_value="1.1.1.1"),
    # )
    # @patch(
    #     "charm.PostgresqlOperatorCharm.members_ips",
    #     new_callable=PropertyMock,
    # )
    # def test_update_endpoints_with_event(self, _members_ips, _primary_endpoint):
    #     # Mock the members_ips list to simulate different scenarios
    #     # (with and without a replica).
    #     _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]
    #
    #     # Add two different relations.
    #     self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
    #     self.another_rel_id = self.harness.add_relation(RELATION_NAME, "application")
    #
    #     # Define a mock relation changed event to be used in the subsequent update endpoints calls.
    #     mock_event = Mock()
    #     # Set the app, id and the initial data for the relation.
    #     mock_event.app = self.harness.charm.model.get_app("application")
    #     mock_event.relation.id = self.rel_id
    #
    #     # Test with both a primary and a replica.
    #     # Update the endpoints with the event and check that it updated
    #     # only the right relation databag (the one from the event).
    #     self.provider.update_endpoints(mock_event)
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.another_rel_id, self.app),
    #         {},
    #     )
    #
    #     # Also test with only a primary instance.
    #     self.provider.update_endpoints(mock_event)
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432"},
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.another_rel_id, self.app),
    #         {},
    #     )
    #
    # @patch(
    #     "charm.PostgresqlOperatorCharm.primary_endpoint",
    #     new_callable=PropertyMock(return_value="1.1.1.1"),
    # )
    # @patch(
    #     "charm.PostgresqlOperatorCharm.members_ips",
    #     new_callable=PropertyMock,
    # )
    # def test_update_endpoints_without_event(self, _members_ips, _primary_endpoint):
    #     # Mock the members_ips list to simulate different scenarios
    #     # (with and without a replica).
    #     _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]
    #
    #     # Add two different relations.
    #     self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
    #     self.another_rel_id = self.harness.add_relation(RELATION_NAME, "application")
    #
    #     # Test with both a primary and a replica.
    #     # Update the endpoints and check that all relations' databags are updated.
    #     self.provider.update_endpoints()
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.another_rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
    #     )
    #
    #     # Also test with only a primary instance.
    #     self.provider.update_endpoints()
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432"},
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.another_rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432"},
    #     )
