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
from constants import PEER
from tests.helpers import patch_network_get

DATABASE = "test_database"
EXTRA_USER_ROLES = "CREATEDB,CREATEROLE"
RELATION_NAME = "database"
POSTGRESQL_VERSION = "12"


@patch_network_get(private_address="1.1.1.1")
class TestPostgreSQLProvider(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "application")
        self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
        self.harness.add_relation_unit(self.rel_id, "application/0")
        self.harness.set_leader(True)
        self.harness.begin()
        self.harness.update_relation_data(
            self.peer_rel_id,
            self.harness.charm.app.name,
            {"cluster_initialised": "True"},
        )

    def request_database(self):
        # Reset the charm status.
        self.harness.model.unit.status = ActiveStatus()

        # Reset the application databag.
        self.harness.update_relation_data(
            self.rel_id,
            "application",
            {"database": "", "extra-user-roles": ""},
        )

        # Reset the database databag.
        self.harness.update_relation_data(
            self.rel_id,
            self.harness.charm.app.name,
            {
                "data": "",
                "username": "",
                "password": "",
                "version": "",
            },
        )

        # Simulate the request of a new database plus extra user roles.
        self.harness.update_relation_data(
            self.rel_id,
            "application",
            {"database": DATABASE, "extra-user-roles": EXTRA_USER_ROLES},
        )

    @patch("charm.PostgreSQLProvider.update_endpoints")
    @patch("relations.postgresql_provider.new_password", return_value="test-password")
    @patch.object(EventBase, "defer")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    def test_on_database_requested(
        self, _member_started, _defer, _new_password, _update_endpoints
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _member_started.side_effect = [False, True, True, True, True]
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

            # Request it again when the database is ready.
            self.request_database()

            # Assert that the correct calls were made.
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.create_user.assert_called_once_with(
                user, "test-password", extra_user_roles=EXTRA_USER_ROLES
            )
            postgresql_mock.create_database.assert_called_once_with(DATABASE, user)
            postgresql_mock.get_postgresql_version.assert_called_once()
            _update_endpoints.assert_called_once()

            # Assert that the relation data was updated correctly.
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.harness.charm.app.name),
                {
                    "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
                    "username": user,
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
                self.harness.get_relation_data(self.rel_id, self.harness.charm.app.name),
                {
                    "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
                },
            )

            # BlockedStatus due to a PostgreSQLCreateDatabaseError.
            self.request_database()
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))
            # No data is set in the databag by the database.
            self.assertEqual(
                self.harness.get_relation_data(self.rel_id, self.harness.charm.app.name),
                {
                    "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
                },
            )

            # BlockedStatus due to a PostgreSQLGetPostgreSQLVersionError.
            self.request_database()
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    @patch.object(EventBase, "defer")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    def test_on_relation_broken(self, _member_started, _defer):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _member_started.side_effect = [False, True, True]
            postgresql_mock.delete_user = PropertyMock(
                side_effect=[None, PostgreSQLDeleteUserError]
            )

            # Break the relation before the database is ready.
            self.harness.remove_relation(self.rel_id)
            _defer.assert_called_once()

            # Assert that the correct calls were made after a relation broken event.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.delete_user.assert_called_once_with(user)

            # Test a failed user deletion.
            self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
            self.harness.remove_relation(self.rel_id)
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    def update_endpoints_with_event(self):
        pass

    def update_endpoints_without_event(self):
        pass
