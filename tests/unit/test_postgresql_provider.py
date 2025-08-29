# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import Mock, PropertyMock, patch

import pytest
from charms.postgresql_k8s.v1.postgresql import (
    ACCESS_GROUP_RELATION,
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

DATABASE = "test_database"
EXTRA_USER_ROLES = "CREATEDB,CREATEROLE"
RELATION_NAME = "database"
POSTGRESQL_VERSION = "12"

CLUSTER_STATUS = [
    {
        "name": "postgresql-0",
        "role": "leader",
        "state": "running",
    },
    {
        "name": "postgresql-1",
        "role": "replica",
        "state": "running",
    },
    {
        "name": "postgresql-2",
        "role": "replica",
        "state": "running",
    },
]


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
    harness.add_relation_unit(peer_rel_id, f"{harness.charm.app.name}/0")
    harness.add_relation_unit(peer_rel_id, f"{harness.charm.app.name}/1")
    harness.add_relation_unit(peer_rel_id, f"{harness.charm.app.name}/2")
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
        {"data": "", "username": "", "password": "", "uris": "", "version": "", "database": ""},
    )

    # Simulate the request of a new database plus extra user roles.
    _harness.update_relation_data(
        rel_id,
        "application",
        {"database": DATABASE, "extra-user-roles": EXTRA_USER_ROLES},
    )


def test_on_database_requested(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
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
        patch(
            "charm.PostgresqlOperatorCharm.generate_user_hash",
            new_callable=PropertyMock,
            return_value="relhash",
        ),
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        peer_rel_id = harness.model.get_relation(PEER).id
        with harness.hooks_disabled():
            harness.update_relation_data(peer_rel_id, "postgresql", {"user_hash": "relhash"})
            harness.update_relation_data(peer_rel_id, "postgresql/0", {"user_hash": "relhash"})
            harness.update_relation_data(peer_rel_id, "postgresql/1", {"user_hash": "relhash"})
            harness.update_relation_data(peer_rel_id, "postgresql/2", {"user_hash": "relhash"})
        # Set some side effects to test multiple situations.
        _member_started.side_effect = [False, True, True, True, True, True]
        _primary_endpoint.side_effect = [
            None,
            "1.1.1.1",
            "1.1.1.1",
            "1.1.1.1",
            "1.1.1.1",
            "1.1.1.1",
        ]
        postgresql_mock.create_user = PropertyMock(
            side_effect=[None, PostgreSQLCreateUserError, None]
        )
        postgresql_mock.create_database = PropertyMock(
            side_effect=[None, PostgreSQLCreateDatabaseError, None, None]
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
        assert _defer.call_count == 2

        # Request it again when the database is ready.
        request_database(harness)

        # Assert that the correct calls were made.
        user = f"relation-{rel_id}"
        expected_user_roles = [role.lower() for role in EXTRA_USER_ROLES.split(",")]
        expected_user_roles.append(ACCESS_GROUP_RELATION)
        postgresql_mock.create_user.assert_called_once_with(
            user,
            "test-password",
            extra_user_roles=expected_user_roles,
            database="test_database",
        )
        postgresql_mock.create_database.assert_called_once_with(
            DATABASE,
            plugins=["pgaudit"],
        )
        postgresql_mock.get_postgresql_version.assert_called_once()
        _update_endpoints.assert_called_once()

        # Assert that the relation data was updated correctly.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
            "username": user,
            "password": "test-password",
            "version": POSTGRESQL_VERSION,
            "database": f"{DATABASE}",
        }

        # Assert no BlockedStatus was set.
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # BlockedStatus due to a PostgreSQLCreateUserError.
        request_database(harness)
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # No data is set in the databag by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
        }

        # BlockedStatus due to a PostgreSQLCreateDatabaseError.
        request_database(harness)
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # No data is set in the databag by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
        }

        # BlockedStatus due to a PostgreSQLGetPostgreSQLVersionError.
        request_database(harness)
        assert isinstance(harness.model.unit.status, BlockedStatus)


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


def test_update_endpoints_with_event(harness: Harness):
    with (
        patch("charm.Patroni.cluster_status") as cluster_status,
        patch(
            "relations.postgresql_provider.DatabaseProvides.fetch_my_relation_data"
        ) as _fetch_my_relation_data,
    ):
        _fetch_my_relation_data.return_value = {
            2: {
                "password": "test_password",
                "username": "relation-2",
            }
        }

        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        rel_id = harness.model.get_relation(RELATION_NAME).id

        # Add two different relations.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        another_rel_id = harness.add_relation(RELATION_NAME, "application")
        peer_rel_id = harness.model.get_relation(PEER).id
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": "test_db", "extra-user-roles": ""},
            )
            harness.update_relation_data(
                peer_rel_id, f"{harness.charm.app.name}/0", {f"{RELATION_NAME}-address": "1.1.1.1"}
            )
            harness.update_relation_data(
                peer_rel_id, f"{harness.charm.app.name}/1", {f"{RELATION_NAME}-address": "2.2.2.2"}
            )

        # Define a mock relation changed event to be used in the subsequent update endpoints calls.
        mock_event = Mock()
        # Set the app, id and the initial data for the relation.
        mock_event.app = harness.charm.model.get_app("application")
        mock_event.relation.id = rel_id

        # Test with both a primary and a replica.
        # Update the endpoints with the event and check that it updated
        # only the right relation databag (the one from the event).
        cluster_status.return_value = CLUSTER_STATUS[:2]
        harness.charm.postgresql_client_relation.update_endpoints(mock_event)
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432",
            "uris": "postgresql://relation-2:test_password@1.1.1.1:5432/test_db",
            "tls": "False",
        }
        assert harness.get_relation_data(another_rel_id, harness.charm.app.name) == {}
        _fetch_my_relation_data.assert_called_once_with([2], ["username", "password"])

        # Also test with only a primary instance.
        cluster_status.return_value = CLUSTER_STATUS[:1]
        harness.charm.postgresql_client_relation.update_endpoints(mock_event)
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "1.1.1.1:5432",
            "uris": "postgresql://relation-2:test_password@1.1.1.1:5432/test_db",
            "tls": "False",
        }
        assert harness.get_relation_data(another_rel_id, harness.charm.app.name) == {}


def test_update_endpoints_without_event(harness):
    with (
        patch("charm.Patroni.cluster_status") as cluster_status,
        patch(
            "relations.postgresql_provider.DatabaseProvides.fetch_my_relation_data"
        ) as _fetch_my_relation_data,
    ):
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        rel_id = harness.model.get_relation(RELATION_NAME).id

        # Don't set data if no password
        cluster_status.return_value = []
        _fetch_my_relation_data.return_value.get.return_value = {}

        harness.charm.postgresql_client_relation.update_endpoints()
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {}

        _fetch_my_relation_data.reset_mock()
        _fetch_my_relation_data.return_value = {
            2: {
                "password": "test_password",
                "username": "relation-2",
            },
            3: {
                "password": "test_password",
                "username": "relation-3",
            },
        }

        # Add two different relations.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        another_rel_id = harness.add_relation(RELATION_NAME, "application")

        relation_ids = [rel.id for rel in harness.charm.model.relations[RELATION_NAME]]
        other_rel_ids = set(relation_ids) - set({rel_id, another_rel_id})
        peer_rel_id = harness.model.get_relation(PEER).id

        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id, f"{harness.charm.app.name}/0", {f"{RELATION_NAME}-address": "1.1.1.1"}
            )
            harness.update_relation_data(
                peer_rel_id, f"{harness.charm.app.name}/1", {f"{RELATION_NAME}-address": "2.2.2.2"}
            )
            harness.update_relation_data(
                peer_rel_id, f"{harness.charm.app.name}/2", {f"{RELATION_NAME}-address": "3.3.3.3"}
            )
            for relation_id in other_rel_ids:
                harness.update_relation_data(
                    relation_id,
                    "application",
                    {"database": "some_db", "extra-user-roles": ""},
                )

            harness.update_relation_data(
                rel_id,
                "application",
                {"database": "test_db", "extra-user-roles": ""},
            )
            harness.update_relation_data(
                another_rel_id,
                "application",
                {"database": "test_db2", "extra-user-roles": ""},
            )

        # Test with both a primary and a replica.
        # Update the endpoints and check that all relations' databags are updated.
        cluster_status.return_value = CLUSTER_STATUS[:2]
        harness.charm.postgresql_client_relation.update_endpoints()
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432",
            "uris": "postgresql://relation-2:test_password@1.1.1.1:5432/test_db",
            "tls": "False",
        }
        assert harness.get_relation_data(another_rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432",
            "uris": "postgresql://relation-3:test_password@1.1.1.1:5432/test_db2",
            "tls": "False",
        }
        _fetch_my_relation_data.assert_called_once_with(None, ["username", "password"])

        # Filter out missing replica
        harness.charm.postgresql_client_relation.update_endpoints()
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432",
            "uris": "postgresql://relation-2:test_password@1.1.1.1:5432/test_db",
            "tls": "False",
        }
        assert harness.get_relation_data(another_rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432",
            "uris": "postgresql://relation-3:test_password@1.1.1.1:5432/test_db2",
            "tls": "False",
        }

        # Don't filter if unable to get cluster status
        cluster_status.return_value = CLUSTER_STATUS
        harness.charm.postgresql_client_relation.update_endpoints()
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432,3.3.3.3:5432",
            "uris": "postgresql://relation-2:test_password@1.1.1.1:5432/test_db",
            "tls": "False",
        }
        assert harness.get_relation_data(another_rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "2.2.2.2:5432,3.3.3.3:5432",
            "uris": "postgresql://relation-3:test_password@1.1.1.1:5432/test_db2",
            "tls": "False",
        }

        # Also test with only a primary instance.
        cluster_status.return_value = CLUSTER_STATUS[:1]
        harness.charm.postgresql_client_relation.update_endpoints()
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "1.1.1.1:5432",
            "uris": "postgresql://relation-2:test_password@1.1.1.1:5432/test_db",
            "tls": "False",
        }
        assert harness.get_relation_data(another_rel_id, harness.charm.app.name) == {
            "endpoints": "1.1.1.1:5432",
            "read-only-endpoints": "1.1.1.1:5432",
            "uris": "postgresql://relation-3:test_password@1.1.1.1:5432/test_db2",
            "tls": "False",
        }
