# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import ops.testing
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
RELATION_NAME = "db"
POSTGRESQL_VERSION = "12"


@patch_network_get(private_address="1.1.1.1")
class TestDbProvides(unittest.TestCase):
    def setUp(self):
        ops.testing.SIMULATE_CAN_CONNECT = True
        self.addCleanup(setattr, ops.testing, "SIMULATE_CAN_CONNECT", False)

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
            user = f"relation-{self.rel_id}"
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

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken(self, _on_relation_departed, _member_started, _primary_endpoint):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _member_started.side_effect = [False, True, True, True]
            _primary_endpoint.side_effect = [None, {"1.1.1.1"}, {"1.1.1.1"}]
            postgresql_mock.delete_user = PropertyMock(
                side_effect=[None, PostgreSQLDeleteUserError]
            )

            # Break the relation before the database is ready.
            self.harness.remove_relation(self.rel_id)
            postgresql_mock.delete_user.assert_not_called()

            # Break the relation before primary endpoint is available.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            postgresql_mock.delete_user.assert_not_called()

            # Assert that the correct calls were made after a relation broken event.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            user = f"relation-{self.rel_id}"
            postgresql_mock.delete_user.assert_called_once_with(user)

            # Test a failed user deletion.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm._has_blocked_status", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken_extensions_unblock(
        self, _on_relation_departed, _member_started, _primary_endpoint, _has_blocked_status
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _has_blocked_status.return_value = True
            _member_started.return_value = True
            _primary_endpoint.return_value = {"1.1.1.1"}
            postgresql_mock.delete_user = PropertyMock(return_value=None)
            self.harness.model.unit.status = BlockedStatus("extensions requested through relation")
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application",
                    {"database": DATABASE, "extensions": "test"},
                )

            # Break the relation before the database is ready.
            self.harness.remove_relation(self.rel_id)
            self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

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
    def test_update_endpoints_with_event(self, _members_ips, _primary_endpoint, _get_state):
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]

        # Add two different relations.
        self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
        self.another_rel_id = self.harness.add_relation(RELATION_NAME, "application")

        # Define a mock relation changed event to be used in the subsequent update endpoints calls.
        mock_event = MagicMock()
        mock_event.relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)

        # Set some data to be used and compared in the relations.
        password = "test-password"
        master = (
            f"dbname={DATABASE} fallback_application_name=application host=1.1.1.1 "
            f"password={password} port={DATABASE_PORT} user="
        )
        standbys = (
            f"dbname={DATABASE} fallback_application_name=application host=2.2.2.2 "
            f"password={password} port={DATABASE_PORT} user="
        )

        # Set some required data before update_endpoints is called.
        for rel_id in [self.rel_id, self.another_rel_id]:
            self.harness.update_relation_data(
                rel_id,
                self.app,
                {
                    "user": f"relation-{rel_id}",
                    "password": password,
                    "database": DATABASE,
                },
            )

        # Test with both a primary and a replica.
        # Update the endpoints with the event and check that it updated only
        # the right relation databags (the app and unit databags from the event).
        self.legacy_db_relation.update_endpoints(mock_event)
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Get the relation data and set the expected username based on the relation id.
            relation_data = self.harness.get_relation_data(rel_id, self.app)
            user = f"relation-{rel_id}"

            # Set the assert function based on each relation (whether it should have data).
            assert_based_on_relation = (
                self.assertTrue if rel_id == self.rel_id else self.assertFalse
            )

            # Check that the application relation databag contains (or not) the endpoints.
            assert_based_on_relation(
                "master" in relation_data and master + user == relation_data["master"]
            )
            assert_based_on_relation(
                "standbys" in relation_data and standbys + user == relation_data["standbys"]
            )

            # Check that the unit relation databag contains (or not) the endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            assert_based_on_relation(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            assert_based_on_relation(
                "standbys" in unit_relation_data
                and standbys + user == unit_relation_data["standbys"]
            )

        # Also test with only a primary instance.
        self.legacy_db_relation.update_endpoints(mock_event)
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Get the relation data and set the expected username based on the relation id.
            relation_data = self.harness.get_relation_data(rel_id, self.app)
            user = f"relation-{rel_id}"

            # Set the assert function based on each relation (whether it should have data).
            assert_based_on_relation = (
                self.assertTrue if rel_id == self.rel_id else self.assertFalse
            )

            # Check that the application relation databag contains (or not) the endpoints.
            assert_based_on_relation(
                "master" in relation_data and master + user == relation_data["master"]
            )
            self.assertTrue("standbys" not in relation_data)

            # Check that the unit relation databag contains only the read/write (master) endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            assert_based_on_relation(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            self.assertTrue("standbys" not in unit_relation_data)

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
            f"password={password} port={DATABASE_PORT} user="
        )
        standbys = (
            f"dbname={DATABASE} fallback_application_name=application host=2.2.2.2 "
            f"password={password} port={DATABASE_PORT} user="
        )

        # Set some required data before update_endpoints is called.
        for rel_id in [self.rel_id, self.another_rel_id]:
            self.harness.update_relation_data(
                rel_id,
                self.app,
                {
                    "user": f"relation-{rel_id}",
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
            user = f"relation-{rel_id}"

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
            user = f"relation-{rel_id}"

            # Check that the application relation databag contains the endpoints.
            self.assertTrue("master" in relation_data and master + user == relation_data["master"])
            self.assertTrue("standbys" not in relation_data)

            # Check that the unit relation databag contains only the read/write (master) endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            self.assertTrue(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            self.assertTrue("standbys" not in unit_relation_data)
