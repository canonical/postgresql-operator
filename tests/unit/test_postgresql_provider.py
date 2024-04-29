# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

import pytest
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLGetPostgreSQLVersionError,
    PostgreSQLListUsersError,
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

# used for assert functions
tc = TestCase()


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    harness.set_leader(True)
    harness.begin()

    # Define some relations.
    rel_id = harness.add_relation(RELATION_NAME, "application")
    harness.add_relation_unit(rel_id, "application/0")
    peer_rel_id = harness.add_relation(PEER, harness.charm.app.name)
    harness.add_relation_unit(peer_rel_id, harness.charm.unit.name)
    harness.update_relation_data(
        peer_rel_id,
        harness.charm.app.name,
        {"cluster_initialised": "True"},
    )
    yield harness
    harness.cleanup()


def request_database(_harness):
    # Reset the charm status.
    rel_id = _harness.model.get_relation(RELATION_NAME).id
    _harness.model.unit.status = ActiveStatus()

    # Reset the application databag.
    _harness.update_relation_data(
        rel_id,
        "application",
        {"database": "", "extra-user-roles": ""},
    )

    # Reset the database databag.
    _harness.update_relation_data(
        rel_id,
        _harness.charm.app.name,
        {"data": "", "username": "", "password": "", "version": "", "database": ""},
    )

    # Simulate the request of a new database plus extra user roles.
    _harness.update_relation_data(
        rel_id,
        "application",
        {"database": DATABASE, "extra-user-roles": EXTRA_USER_ROLES},
    )


@patch_network_get(private_address="1.1.1.1")
def test_on_database_requested(harness):
    with (
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("subprocess.check_output", return_value=b"C"),
        patch("charm.PostgreSQLProvider.update_endpoints") as _update_endpoints,
        patch(
            "relations.postgresql_provider.new_password", return_value="test-password"
        ) as _new_password,
        patch.object(EventBase, "defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
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
        request_database(harness)
        _defer.assert_called_once()

        # Request a database before primary endpoint is available.
        request_database(harness)
        tc.assertEqual(_defer.call_count, 2)

        # Request it again when the database is ready.
        request_database(harness)

        # Assert that the correct calls were made.
        user = f"relation-{rel_id}"
        postgresql_mock.create_user.assert_called_once_with(
            user, "test-password", extra_user_roles=EXTRA_USER_ROLES
        )
        database_relation = harness.model.get_relation(RELATION_NAME)
        client_relations = [database_relation]
        postgresql_mock.create_database.assert_called_once_with(
            DATABASE, user, plugins=[], client_relations=client_relations
        )
        postgresql_mock.get_postgresql_version.assert_called_once()
        _update_endpoints.assert_called_once()

        # Assert that the relation data was updated correctly.
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {
                "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
                "username": user,
                "password": "test-password",
                "version": POSTGRESQL_VERSION,
                "database": f"{DATABASE}",
            },
        )

        # Assert no BlockedStatus was set.
        tc.assertFalse(isinstance(harness.model.unit.status, BlockedStatus))

        # BlockedStatus due to a PostgreSQLCreateUserError.
        request_database(harness)
        tc.assertTrue(isinstance(harness.model.unit.status, BlockedStatus))
        # No data is set in the databag by the database.
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {
                "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
            },
        )

        # BlockedStatus due to a PostgreSQLCreateDatabaseError.
        request_database(harness)
        tc.assertTrue(isinstance(harness.model.unit.status, BlockedStatus))
        # No data is set in the databag by the database.
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {
                "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
            },
        )

        # BlockedStatus due to a PostgreSQLGetPostgreSQLVersionError.
        request_database(harness)
        tc.assertTrue(isinstance(harness.model.unit.status, BlockedStatus))


@patch_network_get(private_address="1.1.1.1")
def test_oversee_users(harness):
    with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
        # Create two relations and add the username in their databags.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        harness.update_relation_data(
            rel_id,
            harness.charm.app.name,
            {"username": f"relation-{rel_id}"},
        )
        another_rel_id = harness.add_relation(RELATION_NAME, "application")
        harness.update_relation_data(
            another_rel_id,
            harness.charm.app.name,
            {"username": f"relation-{another_rel_id}"},
        )

        # Mock some database calls.
        postgresql_mock.list_users = PropertyMock(
            side_effect=[
                {f"relation-{rel_id}", f"relation-{another_rel_id}", "postgres"},
                {f"relation-{rel_id}", f"relation-{another_rel_id}", "postgres"},
                PostgreSQLListUsersError,
            ]
        )

        # Call the method and check that no users were deleted.
        harness.charm.postgresql_client_relation.oversee_users()
        postgresql_mock.delete_user.assert_not_called()

        # Test again (but removing the relation before calling the method).
        harness.remove_relation(rel_id)
        harness.charm.postgresql_client_relation.oversee_users()
        postgresql_mock.delete_user.assert_called_once_with(f"relation-{rel_id}")

        # And test that no delete call is made if the users list couldn't be retrieved.
        harness.charm.postgresql_client_relation.oversee_users()
        postgresql_mock.delete_user.assert_called_once()  # Only the previous call.


@patch_network_get(private_address="1.1.1.1")
def test_update_endpoints_with_event(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value="1.1.1.1"),
        ) as _primary_endpoint,
        patch(
            "charm.PostgresqlOperatorCharm.members_ips",
            new_callable=PropertyMock,
        ) as _members_ips,
        patch("charm.Patroni.get_primary", new_callable=PropertyMock) as _get_primary,
    ):
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        rel_id = harness.model.get_relation(RELATION_NAME).id
        _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]

        # Add two different relations.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        another_rel_id = harness.add_relation(RELATION_NAME, "application")

        # Define a mock relation changed event to be used in the subsequent update endpoints calls.
        mock_event = Mock()
        # Set the app, id and the initial data for the relation.
        mock_event.app = harness.charm.model.get_app("application")
        mock_event.relation.id = rel_id

        # Test with both a primary and a replica.
        # Update the endpoints with the event and check that it updated
        # only the right relation databag (the one from the event).
        harness.charm.postgresql_client_relation.update_endpoints(mock_event)
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
        )
        tc.assertEqual(
            harness.get_relation_data(another_rel_id, harness.charm.app.name),
            {},
        )

        # Also test with only a primary instance.
        harness.charm.postgresql_client_relation.update_endpoints(mock_event)
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {"endpoints": "1.1.1.1:5432"},
        )
        tc.assertEqual(
            harness.get_relation_data(another_rel_id, harness.charm.app.name),
            {},
        )


@patch_network_get(private_address="1.1.1.1")
def test_update_endpoints_without_event(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value="1.1.1.1"),
        ) as _primary_endpoint,
        patch(
            "charm.PostgresqlOperatorCharm.members_ips",
            new_callable=PropertyMock,
        ) as _members_ips,
        patch("charm.Patroni.get_primary", new_callable=PropertyMock) as _get_primary,
    ):
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [{"1.1.1.1", "2.2.2.2"}, {"1.1.1.1"}]
        rel_id = harness.model.get_relation(RELATION_NAME).id

        # Add two different relations.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        another_rel_id = harness.add_relation(RELATION_NAME, "application")

        # Test with both a primary and a replica.
        # Update the endpoints and check that all relations' databags are updated.
        harness.charm.postgresql_client_relation.update_endpoints()
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
        )
        tc.assertEqual(
            harness.get_relation_data(another_rel_id, harness.charm.app.name),
            {"endpoints": "1.1.1.1:5432", "read-only-endpoints": "2.2.2.2:5432"},
        )

        # Also test with only a primary instance.
        harness.charm.postgresql_client_relation.update_endpoints()
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app.name),
            {"endpoints": "1.1.1.1:5432"},
        )
        tc.assertEqual(
            harness.get_relation_data(another_rel_id, harness.charm.app.name),
            {"endpoints": "1.1.1.1:5432"},
        )
