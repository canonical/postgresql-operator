# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import Mock

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm


@pytest.fixture(autouse=True)
def harness():
    """Set up the test."""
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    upgrade_relation_id = harness.add_relation("upgrade", "postgresql")
    peer_relation_id = harness.add_relation("database-peers", "postgresql")
    for rel_id in (upgrade_relation_id, peer_relation_id):
        harness.add_relation_unit(rel_id, "postgresql/1")
    with harness.hooks_disabled():
        harness.update_relation_data(upgrade_relation_id, "postgresql/1", {"state": "idle"})
    yield harness
    harness.cleanup()


def test_on_reenable_oversee_users(harness):
    # Fail if unit is not leader
    event = Mock()

    harness.charm.async_replication._on_reenable_oversee_users(event)

    event.fail.assert_called_once_with("Unit is not leader")
    event.fail.reset_mock()

    # Fail if peer data is not set
    with harness.hooks_disabled():
        harness.set_leader()

    harness.charm.async_replication._on_reenable_oversee_users(event)

    event.fail.assert_called_once_with("Oversee users is not suppressed")
    event.fail.reset_mock()

    with harness.hooks_disabled():
        harness.charm._peers.data[harness.charm.app].update({"suppress-oversee-users": "true"})

        harness.charm.async_replication._on_reenable_oversee_users(event)
        assert harness.charm._peers.data[harness.charm.app] == {}
