# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, PropertyMock, patch

from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
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
        self.harness.add_relation_unit(self.peer_rel_id, f"{self.app}/1")
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
        self,
        _member_started,
        _primary_endpoint,
        _defer,
        _new_password,
        _update_endpoints,
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
                self.harness.get_relation_data(self.rel_id, self.unit),
                {
                    "database": DATABASE,
                    "password": "test-password",
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

    def test_get_extensions(self):
        # Test when there are no extensions in the relation databags.
        relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)
        self.assertEqual(
            self.harness.charm.legacy_db_relation._get_extensions(relation), ([], set())
        )

        # Test when there are extensions in the application relation databag.
        extensions = ["", "citext:public", "debversion"]
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                "application",
                {"extensions": ",".join(extensions)},
            )
        self.assertEqual(
            self.harness.charm.legacy_db_relation._get_extensions(relation),
            ([extensions[1], extensions[2]], {extensions[1].split(":")[0], extensions[2]}),
        )

        # Test when there are extensions in the unit relation databag.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                "application",
                {"extensions": ""},
            )
            self.harness.update_relation_data(
                self.rel_id,
                "application/0",
                {"extensions": ",".join(extensions)},
            )
        self.assertEqual(
            self.harness.charm.legacy_db_relation._get_extensions(relation),
            ([extensions[1], extensions[2]], {extensions[1].split(":")[0], extensions[2]}),
        )

        # Test when one of the plugins/extensions is enabled.
        config = """options:
          plugin_citext_enable:
            default: true
            type: boolean
          plugin_debversion_enable:
            default: false
            type: boolean"""
        harness = Harness(PostgresqlOperatorCharm, config=config)
        self.addCleanup(harness.cleanup)
        harness.begin()
        self.assertEqual(
            harness.charm.legacy_db_relation._get_extensions(relation),
            ([extensions[1], extensions[2]], {extensions[2]}),
        )

    def test_set_up_relation(self):
        pass

    @patch("relations.db.DbProvides._check_for_blocking_relations")
    @patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock)
    def test_update_unit_status(self, _is_blocked, _check_for_blocking_relations):
        # Test when the charm is not blocked.
        relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)
        _is_blocked.return_value = False
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        self.assertNotIsInstance(self.harness.charm.unit.status, ActiveStatus)

        # Test when the charm is blocked but not due to extensions request.
        _is_blocked.return_value = True
        self.harness.charm.unit.status = BlockedStatus("fake message")
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        self.assertNotIsInstance(self.harness.charm.unit.status, ActiveStatus)

        # Test when there are relations causing the blocked status.
        self.harness.charm.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        _check_for_blocking_relations.return_value = True
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        self.assertNotIsInstance(self.harness.charm.unit.status, ActiveStatus)

        # Test when there are no relations causing the blocked status anymore.
        _check_for_blocking_relations.reset_mock()
        _check_for_blocking_relations.return_value = False
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        self.assertIsInstance(self.harness.charm.unit.status, ActiveStatus)

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken_extensions_unblock(
        self, _on_relation_departed, _member_started, _primary_endpoint, is_blocked
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            is_blocked.return_value = True
            _member_started.return_value = True
            _primary_endpoint.return_value = {"1.1.1.1"}
            postgresql_mock.delete_user = PropertyMock(return_value=None)
            self.harness.model.unit.status = BlockedStatus(
                "extensions requested through relation, enable them through config options"
            )
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application",
                    {"database": DATABASE, "extensions": "test"},
                )

            # Break the relation that blocked the charm.
            self.harness.remove_relation(self.rel_id)
            self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken_extensions_keep_block(
        self, _on_relation_departed, _member_started, _primary_endpoint, is_blocked
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            is_blocked.return_value = True
            _member_started.return_value = True
            _primary_endpoint.return_value = {"1.1.1.1"}
            postgresql_mock.delete_user = PropertyMock(return_value=None)
            self.harness.model.unit.status = BlockedStatus(
                "extensions requested through relation, enable them through config options"
            )
            with self.harness.hooks_disabled():
                first_rel_id = self.harness.add_relation(RELATION_NAME, "application1")
                self.harness.update_relation_data(
                    first_rel_id,
                    "application1",
                    {"database": DATABASE, "extensions": "test"},
                )
                second_rel_id = self.harness.add_relation(RELATION_NAME, "application2")
                self.harness.update_relation_data(
                    second_rel_id,
                    "application2",
                    {"database": DATABASE, "extensions": "test"},
                )

            event = Mock()
            event.relation.id = first_rel_id
            # Break one of the relations that block the charm.
            self.harness.charm.legacy_db_relation._on_relation_broken(event)
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

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
    @patch("charm.Patroni.get_primary", return_value="postgresql/0")
    def test_update_endpoints_with_relation(
        self, _get_primary, _members_ips, _primary_endpoint, _get_state
    ):
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1"},
            {"1.1.1.1"},
        ]

        # Add two different relations.
        self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
        self.another_rel_id = self.harness.add_relation(RELATION_NAME, "application")

        # Get the relation to be used in the subsequent update endpoints calls.
        relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)

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
            user = f"relation-{rel_id}"
            self.harness.update_relation_data(
                rel_id,
                self.app,
                {
                    "user": user,
                    "password": password,
                    "database": DATABASE,
                },
            )
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.app,
                {
                    user: password,
                    f"{user}-database": DATABASE,
                },
            )

        # Test with both a primary and a replica.
        # Update the endpoints with the event and check that it updated only
        # the right relation databags (the app and unit databags from the event).
        self.legacy_db_relation.update_endpoints(relation)
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel_id}"

            # Set the assert function based on each relation (whether it should have data).
            assert_based_on_relation = (
                self.assertTrue if rel_id == self.rel_id else self.assertFalse
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
        self.legacy_db_relation.update_endpoints(relation)
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel_id}"

            # Set the assert function based on each relation (whether it should have data).
            assert_based_on_relation = (
                self.assertTrue if rel_id == self.rel_id else self.assertFalse
            )

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            assert_based_on_relation(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            assert_based_on_relation(
                "standbys" in unit_relation_data
                and standbys + user == unit_relation_data["standbys"]
            )

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
    @patch("charm.Patroni.get_primary")
    def test_update_endpoints_without_event(
        self, _get_primary, _members_ips, _primary_endpoint, _get_state
    ):
        _get_primary.return_value = self.unit
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1"},
            {"1.1.1.1"},
        ]

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
            user = f"relation-{rel_id}"
            self.harness.update_relation_data(
                rel_id,
                self.app,
                {
                    "user": user,
                    "password": password,
                    "database": DATABASE,
                },
            )
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.app,
                {
                    user: password,
                    f"{user}-database": DATABASE,
                },
            )

        # Test with both a primary and a replica.
        # Update the endpoints and check that all relations' databags are updated.
        self.legacy_db_relation.update_endpoints()
        for rel_id in [self.rel_id, self.another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel_id}"

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
            # Set the expected username based on the relation id.
            user = f"relation-{rel_id}"

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = self.harness.get_relation_data(rel_id, self.unit)
            self.assertTrue(
                "master" in unit_relation_data and master + user == unit_relation_data["master"]
            )
            self.assertTrue(
                "standbys" in unit_relation_data
                and standbys + user == unit_relation_data["standbys"]
            )
