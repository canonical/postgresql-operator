# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, PropertyMock, patch

from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import DATABASE_PORT, PEER
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
        self.legacy_db_relation = self.harness.charm.legacy_db_relation

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
    #     "charm.DbProvides._get_state",
    #     side_effect="postgresql/0",
    # )
    # @patch(
    #     "charm.PostgresqlOperatorCharm.primary_endpoint",
    #     new_callable=PropertyMock(return_value="1.1.1.1"),
    # )
    # @patch(
    #     "charm.PostgresqlOperatorCharm.members_ips",
    #     new_callable=PropertyMock,
    # )
    # def test_update_endpoints_with_event(self, _members_ips, _primary_endpoint, _get_state):
    #     # Mock the members_ips list to simulate different scenarios
    #     # (with and without a replica).
    #     _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]
    #
    #     # Add two different relations.
    #     self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
    #     self.another_rel_id = self.harness.add_relation(RELATION_NAME, "application")
    #
    #     # Define a mock relation changed event to be used in the subsequent update endpoints calls.
    #     user = f"relation_id_{self.rel_id}"
    #     password = "test-password"
    #     mock_event = Mock()
    #     # Set the app, id and the initial data for the relation.
    #     mock_event.app = self.harness.charm.model.get_app("application")
    #     mock_event.unit = self.harness.charm.model.get_app("application/0")
    #     mock_event.relation.id = self.rel_id
    #     mock_event.relation.data = self.harness.get_relation_data(self.rel_id, self.unit)
    #     mock_event.relation.data = {
    #         self.harness.charm.app: {
    #             "database": DATABASE,
    #             "user": user,
    #             "password": password,
    #         },
    #         # self.harness.charm.app: self.harness.get_relation_data(self.rel_id, self.app),
    #         self.harness.charm.unit: {}  # self.harness.get_relation_data(self.rel_id, self.unit),
    #     }
    #     # self.harness.update_relation_data(self.rel_id, self.harness.charm.app, user, password)
    #
    #     # Test with both a primary and a replica.
    #     # Update the endpoints with the event and check that it updated only
    #     # the right relation databags (the app and unit databags from the event).
    #     self.legacy_db_relation.update_endpoints(mock_event)
    #     print(self.harness.get_relation_data(self.rel_id, self.harness.charm.app.name))
    #     print(self.harness.get_relation_data(self.rel_id, self.harness.charm.unit.name))
    #     print(self.harness.get_relation_data(self.another_rel_id, self.harness.charm.app.name))
    #     print(self.harness.get_relation_data(self.another_rel_id, self.harness.charm.unit.name))
    #     print(self.harness.get_relation_data(self.rel_id, "application"))
    #     print(self.harness.get_relation_data(self.rel_id, "application/0"))
    #     print(self.harness.get_relation_data(self.another_rel_id, "application"))
    #     print(self.harness.get_relation_data(self.another_rel_id, "application/0"))
    #     print(self.rel_id)
    #     print(self.app)
    #     print(self.unit)
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         self.harness.get_relation_data(self.rel_id, self.unit),
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.another_rel_id, self.app),
    #         {},
    #     )
    #
    #     # Also test with only a primary instance.
    #     self.legacy_db_relation.update_endpoints(mock_event)
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.rel_id, self.app),
    #         {"endpoints": "1.1.1.1:5432"},
    #     )
    #     self.assertEqual(
    #         self.harness.get_relation_data(self.another_rel_id, self.app),
    #         {},
    #     )

    @patch(
        "charm.DbProvides._get_state",
        side_effect="postgresql/0",
    )
    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock(return_value="1.1.1.1"),
    )
    @patch(
        "charm.PostgresqlOperatorCharm.members_ips",
        new_callable=PropertyMock,
    )
    def test_update_endpoints_without_event(self, _members_ips, _primary_endpoint, _get_state):
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]

        # Add two different relations.
        self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
        self.another_rel_id = self.harness.add_relation(RELATION_NAME, "application")

        # Set some data to be used and compared in the relations.
        password = "test-password"
        master = (
            f"dbname={DATABASE} fallback_application_name=application host=1.1.1.1 "
            f"password={password} port=5432 user="
        )
        standbys = (
            f"dbname={DATABASE} fallback_application_name=application host=2.2.2.2 "
            f"password={password} port=5432 user="
        )

        # Set some required data before update_endpoints is called.
        for rel_id in [self.rel_id, self.another_rel_id]:
            self.harness.update_relation_data(
                rel_id,
                self.app,
                {
                    "user": f"relation_id_{rel_id}",
                    "password": password,
                    "database": DATABASE,
                },
            )

        # Test with both a primary and a replica.
        # Update the endpoints and check that all relations' databags are updated.
        self.legacy_db_relation.update_endpoints()
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Get the relation data and set the expected username based on the relation id.
            relation_data = self.harness.get_relation_data(rel_id, self.app)
            user = f"relation_id_{rel_id}"

            # Check that the application relation databag contains the endpoints.
            self.assertTrue("master" in relation_data and master + user == relation_data["master"])
            self.assertTrue(
                "standbys" in relation_data and standbys + user == relation_data["standbys"]
            )

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            self.assertTrue(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            self.assertTrue(
                "standbys" in unit_relation_data
                and standbys + user == unit_relation_data["standbys"]
            )

        # Also test with only a primary instance.
        self.legacy_db_relation.update_endpoints()
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Get the relation data and set the expected username based on the relation id.
            relation_data = self.harness.get_relation_data(rel_id, self.app)
            user = f"relation_id_{rel_id}"

            # Check that the application relation databag contains the endpoints.
            self.assertTrue("master" in relation_data and master + user == relation_data["master"])
            self.assertTrue("standbys" not in relation_data)

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            self.assertTrue(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            self.assertTrue("standbys" not in unit_relation_data)
