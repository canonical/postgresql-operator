# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import Mock, PropertyMock, patch

import pytest
from charms.postgresql_k8s.v0.postgresql import (
    ACCESS_GROUP_RELATION,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import DATABASE_PORT, PEER

DATABASE = "test_database"
RELATION_NAME = "db"
POSTGRESQL_VERSION = "12"


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
    harness.add_relation_unit(peer_rel_id, f"{harness.charm.app.name}/1")
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
    _harness.model.unit.status = ActiveStatus()
    rel_id = _harness.model.get_relation(RELATION_NAME).id

    with _harness.hooks_disabled():
        # Reset the application databag.
        _harness.update_relation_data(
            rel_id,
            "application/0",
            {"database": ""},
        )

        # Reset the database databag.
        _harness.update_relation_data(
            rel_id,
            _harness.charm.app.name,
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
    _harness.update_relation_data(
        rel_id,
        "application/0",
        {"database": DATABASE},
    )


def test_on_relation_changed(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("charm.DbProvides.set_up_relation") as _set_up_relation,
        patch("charm.DbProvides.update_endpoints"),
        patch.object(EventBase, "defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
    ):
        # Set some side effects to test multiple situations.
        _member_started.side_effect = [True, False, True, True]
        postgresql_mock.list_users.return_value = {"relation-0"}
        _primary_endpoint.side_effect = [
            None,
            {"1.1.1.1"},
            {"1.1.1.1"},
            {"1.1.1.1"},
            {"1.1.1.1"},
        ]
        # Request a database to a non leader unit.
        with harness.hooks_disabled():
            harness.set_leader(False)
        request_database(harness)
        _defer.assert_not_called()
        _set_up_relation.assert_not_called()

        # Request a database before the database is ready.
        with harness.hooks_disabled():
            harness.set_leader()
        request_database(harness)
        _defer.assert_called_once()
        _set_up_relation.assert_not_called()

        # Request a database before primary endpoint is available.
        request_database(harness)
        assert _defer.call_count == 2
        _set_up_relation.assert_not_called()

        # Request it again when the database is ready.
        _defer.reset_mock()
        request_database(harness)
        _defer.assert_not_called()
        _set_up_relation.assert_called_once()


def test_get_extensions(harness):
    # Test when there are no extensions in the relation databags.
    rel_id = harness.model.get_relation(RELATION_NAME).id
    relation = harness.model.get_relation(RELATION_NAME, rel_id)
    assert harness.charm.legacy_db_relation._get_extensions(relation) == ([], set())

    # Test when there are extensions in the application relation databag.
    extensions = ["", "citext:public", "debversion"]
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id,
            "application",
            {"extensions": ",".join(extensions)},
        )
    assert harness.charm.legacy_db_relation._get_extensions(relation) == (
        [extensions[1], extensions[2]],
        {extensions[1].split(":")[0], extensions[2]},
    )

    # Test when there are extensions in the unit relation databag.
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id,
            "application",
            {"extensions": ""},
        )
        harness.update_relation_data(
            rel_id,
            "application/0",
            {"extensions": ",".join(extensions)},
        )
    assert harness.charm.legacy_db_relation._get_extensions(relation) == (
        [extensions[1], extensions[2]],
        {extensions[1].split(":")[0], extensions[2]},
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
    harness.cleanup()
    harness.begin()
    assert harness.charm.legacy_db_relation._get_extensions(relation) == (
        [extensions[1], extensions[2]],
        {extensions[2]},
    )


def test_set_up_relation(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("subprocess.check_output", return_value=b"C"),
        patch("relations.db.DbProvides._update_unit_status") as _update_unit_status,
        patch("relations.db.new_password", return_value="test-password") as _new_password,
        patch("relations.db.DbProvides._get_extensions") as _get_extensions,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        # Define some mocks' side effects.
        extensions = ["citext:public", "debversion"]
        _get_extensions.side_effect = [
            (extensions, {"debversion"}),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
        ]
        postgresql_mock.create_user = PropertyMock(
            side_effect=[None, None, None, PostgreSQLCreateUserError, None, None]
        )
        postgresql_mock.create_database = PropertyMock(
            side_effect=[None, None, None, PostgreSQLCreateDatabaseError, None]
        )

        # Assert no operation is done when at least one of the requested extensions
        # is disabled.
        relation = harness.model.get_relation(RELATION_NAME, rel_id)
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_user.assert_not_called()
        postgresql_mock.create_database.assert_not_called()
        postgresql_mock.get_postgresql_version.assert_not_called()
        _update_unit_status.assert_not_called()

        # Assert that the correct calls were made in a successful setup.
        harness.charm.unit.status = ActiveStatus()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": DATABASE},
            )
        assert harness.charm.legacy_db_relation.set_up_relation(relation)
        user = f"relation-{rel_id}"
        postgresql_mock.create_user.assert_called_once_with(
            user, "test-password", False, extra_user_roles=[ACCESS_GROUP_RELATION]
        )
        postgresql_mock.create_database.assert_called_once_with(
            DATABASE, user, plugins=["pgaudit"], client_relations=[relation]
        )
        _update_unit_status.assert_called_once()
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # Assert that the correct calls were made when the database name is not
        # provided in both application and unit databags.
        postgresql_mock.create_user.reset_mock()
        postgresql_mock.create_database.reset_mock()
        postgresql_mock.get_postgresql_version.reset_mock()
        _update_unit_status.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": ""},
            )
            harness.update_relation_data(
                rel_id,
                "application/0",
                {"database": DATABASE},
            )
        assert harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_user.assert_called_once_with(
            user, "test-password", False, extra_user_roles=[ACCESS_GROUP_RELATION]
        )
        postgresql_mock.create_database.assert_called_once_with(
            DATABASE, user, plugins=["pgaudit"], client_relations=[relation]
        )
        _update_unit_status.assert_called_once()
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # Assert that the correct calls were made when the database name is not provided.
        postgresql_mock.create_user.reset_mock()
        postgresql_mock.create_database.reset_mock()
        postgresql_mock.get_postgresql_version.reset_mock()
        _update_unit_status.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application/0",
                {"database": ""},
            )
        assert harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_user.assert_called_once_with(
            user, "test-password", False, extra_user_roles=[ACCESS_GROUP_RELATION]
        )
        postgresql_mock.create_database.assert_called_once_with(
            "test_database", user, plugins=["pgaudit"], client_relations=[relation]
        )
        _update_unit_status.assert_called_once()
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # BlockedStatus due to a PostgreSQLCreateUserError.
        postgresql_mock.create_database.reset_mock()
        postgresql_mock.get_postgresql_version.reset_mock()
        _update_unit_status.reset_mock()
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_database.assert_not_called()
        _update_unit_status.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)

        # BlockedStatus due to a PostgreSQLCreateDatabaseError.
        harness.charm.unit.status = ActiveStatus()
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        _update_unit_status.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_update_unit_status(harness):
    with (
        patch(
            "relations.db.DbProvides._check_for_blocking_relations"
        ) as _check_for_blocking_relations,
        patch(
            "charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock
        ) as _is_blocked,
    ):
        # Test when the charm is not blocked.
        rel_id = harness.model.get_relation(RELATION_NAME).id
        relation = harness.model.get_relation(RELATION_NAME, rel_id)
        _is_blocked.return_value = False
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        assert not isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the charm is blocked but not due to extensions request.
        _is_blocked.return_value = True
        harness.charm.unit.status = BlockedStatus("fake message")
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        assert not isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when there are relations causing the blocked status.
        harness.charm.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        _check_for_blocking_relations.return_value = True
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        assert not isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when there are no relations causing the blocked status anymore.
        _check_for_blocking_relations.reset_mock()
        _check_for_blocking_relations.return_value = False
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        assert isinstance(harness.charm.unit.status, ActiveStatus)


def test_on_relation_broken_extensions_unblock(harness):
    with (
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock) as is_blocked,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.DbProvides._on_relation_departed") as _on_relation_departed,
    ):
        # Set some side effects to test multiple situations.
        rel_id = harness.model.get_relation(RELATION_NAME).id
        is_blocked.return_value = True
        _member_started.return_value = True
        _primary_endpoint.return_value = {"1.1.1.1"}
        postgresql_mock.delete_user = PropertyMock(return_value=None)
        harness.model.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": DATABASE, "extensions": "test"},
            )

        # Break the relation that blocked the charm.
        harness.remove_relation(rel_id)
        assert isinstance(harness.model.unit.status, ActiveStatus)


def test_on_relation_broken_extensions_keep_block(harness):
    with (
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("charm.DbProvides._on_relation_departed") as _on_relation_departed,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock) as is_blocked,
    ):
        # Set some side effects to test multiple situations.
        is_blocked.return_value = True
        _member_started.return_value = True
        _primary_endpoint.return_value = {"1.1.1.1"}
        postgresql_mock.delete_user = PropertyMock(return_value=None)
        harness.model.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        with harness.hooks_disabled():
            first_rel_id = harness.add_relation(RELATION_NAME, "application1")
            harness.update_relation_data(
                first_rel_id,
                "application1",
                {"database": DATABASE, "extensions": "test"},
            )
            second_rel_id = harness.add_relation(RELATION_NAME, "application2")
            harness.update_relation_data(
                second_rel_id,
                "application2",
                {"database": DATABASE, "extensions": "test"},
            )

        event = Mock()
        event.relation.id = first_rel_id
        # Break one of the relations that block the charm.
        harness.charm.legacy_db_relation._on_relation_broken(event)
        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_update_endpoints_with_relation(harness):
    with (
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("charm.Patroni.get_primary") as _get_primary,
        patch(
            "relations.db.logger",
        ) as _logger,
        patch(
            "charm.PostgresqlOperatorCharm.members_ips",
            new_callable=PropertyMock,
        ) as _members_ips,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value="1.1.1.1"),
        ) as _primary_endpoint,
        patch(
            "charm.DbProvides._get_state",
            side_effect="postgresql/0",
        ) as _get_state,
    ):
        # Set some side effects to test multiple situations.
        postgresql_mock.get_postgresql_version = PropertyMock(
            side_effect=[
                POSTGRESQL_VERSION,
                POSTGRESQL_VERSION,
                PostgreSQLGetPostgreSQLVersionError,
            ]
        )

        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1"},
            {"1.1.1.1"},
        ]

        # Add two different relations.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        another_rel_id = harness.add_relation(RELATION_NAME, "application")

        # Get the relation to be used in the subsequent update endpoints calls.
        relation = harness.model.get_relation(RELATION_NAME, rel_id)

        # Set some data to be used and compared in the relations.
        password = "test-password"
        master = f"dbname={DATABASE} host=1.1.1.1 password={password} port={DATABASE_PORT} user="
        standbys = f"dbname={DATABASE} host=2.2.2.2 password={password} port={DATABASE_PORT} user="

        # Set some required data before update_endpoints is called.
        for rel in [rel_id, another_rel_id]:
            user = f"relation-{rel}"
            harness.charm.set_secret("app", user, password)
            harness.charm.set_secret("app", f"{user}-database", DATABASE)

        # Test with both a primary and a replica.
        # Update the endpoints with the event and check that it updated only
        # the right relation databags (the app and unit databags from the event).
        harness.charm.legacy_db_relation.update_endpoints(relation)
        for rel in [rel_id, another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel}"

            # Check that the unit relation databag contains (or not) the endpoints.
            unit_relation_data = harness.get_relation_data(rel, harness.charm.unit.name)
            if rel == rel_id:
                assert (
                    "master" in unit_relation_data
                    and master + user == unit_relation_data["master"]
                )
                assert (
                    "standbys" in unit_relation_data
                    and standbys + user == unit_relation_data["standbys"]
                )
            else:
                assert not (
                    "master" in unit_relation_data
                    and master + user == unit_relation_data["master"]
                )
                assert not (
                    "standbys" in unit_relation_data
                    and standbys + user == unit_relation_data["standbys"]
                )

        # Also test with only a primary instance.
        harness.charm.legacy_db_relation.update_endpoints(relation)
        for rel in [rel_id, another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel}"

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = harness.get_relation_data(rel, harness.charm.unit.name)
            if rel == rel_id:
                assert (
                    "master" in unit_relation_data
                    and master + user == unit_relation_data["master"]
                )
                assert (
                    "standbys" in unit_relation_data
                    and standbys + user == unit_relation_data["standbys"]
                )
            else:
                assert not (
                    "master" in unit_relation_data
                    and master + user == unit_relation_data["master"]
                )
                assert not (
                    "standbys" in unit_relation_data
                    and standbys + user == unit_relation_data["standbys"]
                )

        # version is not updated due to a PostgreSQLGetPostgreSQLVersionError.
        harness.charm.legacy_db_relation.update_endpoints()
        _logger.exception.assert_called_once_with(
            "Failed to retrieve the PostgreSQL version to initialise/update db relation"
        )


def test_update_endpoints_without_relation(harness):
    with (
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("charm.Patroni.get_primary") as _get_primary,
        patch(
            "relations.db.logger",
        ) as _logger,
        patch(
            "charm.PostgresqlOperatorCharm.members_ips",
            new_callable=PropertyMock,
        ) as _members_ips,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock(return_value="1.1.1.1"),
        ) as _primary_endpoint,
        patch(
            "charm.DbProvides._get_state",
            side_effect="postgresql/0",
        ) as _get_state,
    ):
        # Set some side effects to test multiple situations.
        postgresql_mock.get_postgresql_version = PropertyMock(
            side_effect=[
                POSTGRESQL_VERSION,
                POSTGRESQL_VERSION,
                PostgreSQLGetPostgreSQLVersionError,
            ]
        )
        _get_primary.return_value = harness.charm.unit.name
        # Mock the members_ips list to simulate different scenarios
        # (with and without a replica).
        _members_ips.side_effect = [
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1", "2.2.2.2"},
            {"1.1.1.1"},
            {"1.1.1.1"},
        ]

        # Add two different relations.
        rel_id = harness.add_relation(RELATION_NAME, "application")
        another_rel_id = harness.add_relation(RELATION_NAME, "application")

        # Set some data to be used and compared in the relations.
        password = "test-password"
        master = f"dbname={DATABASE} host=1.1.1.1 password={password} port={DATABASE_PORT} user="
        standbys = f"dbname={DATABASE} host=2.2.2.2 password={password} port={DATABASE_PORT} user="

        # Set some required data before update_endpoints is called.
        for rel in [rel_id, another_rel_id]:
            user = f"relation-{rel}"
            harness.charm.set_secret("app", user, password)
            harness.charm.set_secret("app", f"{user}-database", DATABASE)

        # Test with both a primary and a replica.
        # Update the endpoints and check that all relations' databags are updated.
        harness.charm.legacy_db_relation.update_endpoints()
        for rel in [rel_id, another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel}"

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = harness.get_relation_data(rel, harness.charm.unit.name)
            assert "master" in unit_relation_data and master + user == unit_relation_data["master"]
            assert (
                "standbys" in unit_relation_data
                and standbys + user == unit_relation_data["standbys"]
            )

        # Also test with only a primary instance.
        harness.charm.legacy_db_relation.update_endpoints()
        for rel in [rel_id, another_rel_id]:
            # Set the expected username based on the relation id.
            user = f"relation-{rel}"

            # Check that the unit relation databag contains the endpoints.
            unit_relation_data = harness.get_relation_data(rel, harness.charm.unit.name)
            assert "master" in unit_relation_data and master + user == unit_relation_data["master"]
            assert (
                "standbys" in unit_relation_data
                and standbys + user == unit_relation_data["standbys"]
            )

        # version is not updated due to a PostgreSQLGetPostgreSQLVersionError.
        harness.charm.legacy_db_relation.update_endpoints()
        _logger.exception.assert_called_once_with(
            "Failed to retrieve the PostgreSQL version to initialise/update db relation"
        )


def test_get_allowed_units(harness):
    # No allowed units from the current database application.
    peer_rel_id = harness.model.get_relation(PEER).id
    rel_id = harness.model.get_relation(RELATION_NAME).id
    peer_relation = harness.model.get_relation(PEER, peer_rel_id)
    assert harness.charm.legacy_db_relation._get_allowed_units(peer_relation) == ""

    # List of space separated allowed units from the other application.
    harness.add_relation_unit(rel_id, "application/1")
    db_relation = harness.model.get_relation(RELATION_NAME, rel_id)
    assert (
        harness.charm.legacy_db_relation._get_allowed_units(db_relation)
        == "application/0 application/1"
    )
