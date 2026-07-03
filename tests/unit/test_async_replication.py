# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from ops import Application, ModelError, SecretNotFoundError
from single_kernel_postgresql.config.literals import REPLICATION_CONSUMER_RELATION
from tenacity import RetryError

from src.relations.async_replication import (
    OFFER_SECRET_LABEL,
    READ_ONLY_MODE_BLOCKING_MESSAGE,
    SECRET_LABEL,
    PostgreSQLAsyncReplication,
    _safe_databag_get,
    _same_secret_id,
)

# Several tests (e.g. ``test_on_create_replication``) reassign ``_relation`` on the class
# via ``type(relation)._relation = PropertyMock(...)`` with no cleanup, leaking a mock over
# the real property for later tests. Capture the real property once, before any test runs,
# so a test that needs to exercise the real ``_relation`` can restore it for its own scope.
_REAL_RELATION_PROPERTY = PostgreSQLAsyncReplication.__dict__["_relation"]


def create_mock_unit(name="unit"):
    unit = MagicMock()
    unit.name = name
    return unit


def test_on_secret_changed():
    # 1. relation is None
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    with (
        patch.object(
            PostgreSQLAsyncReplication, "_relation", new_callable=PropertyMock, return_value=None
        ),
        patch("logging.Logger.debug") as mock_debug,
    ):
        relation._on_secret_changed(mock_event)

        mock_debug.assert_called_once_with("Early exit on_secret_changed: No relation found.")
        mock_event.defer.assert_not_called()


def test__configure_primary_cluster():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._configure_primary_cluster(None, mock_event)
    assert result is False

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()
    mock_charm.unit.is_leader.return_value = False
    mock_charm.update_config = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation.is_primary_cluster = MagicMock(return_value=False)
    result = relation._configure_primary_cluster(mock_charm.app, mock_event)
    mock_charm.update_config.assert_called_once()
    assert result is True

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.update_config = MagicMock()
    mock_charm._patroni.get_standby_leader.return_value = True
    mock_charm._patroni.promote_standby_cluster = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation.is_primary_cluster = MagicMock(return_value=True)

    relation._update_primary_cluster_data = MagicMock()

    result = relation._configure_primary_cluster(mock_charm.app, mock_event)

    mock_charm.update_config.assert_called_once()
    relation._update_primary_cluster_data.assert_called_once()
    mock_charm._patroni.promote_standby_cluster()
    assert result is True

    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm.app = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.update_config = MagicMock()
    mock_charm._patroni.get_standby_leader.return_value = None

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation.is_primary_cluster = MagicMock(return_value=True)

    relation._update_primary_cluster_data = MagicMock()

    result = relation._configure_primary_cluster(mock_charm.app, mock_event)

    mock_charm.update_config.assert_called_once()
    relation._update_primary_cluster_data.assert_called_once()
    assert result is True


def test__on_async_relation_departed():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_unit_data = {}
    mock_charm.unit_peer_data = mock_unit_data
    mock_event.departing_unit = MagicMock()
    mock_charm.unit = mock_event.departing_unit

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._on_async_relation_departed(mock_event)
    assert result is None
    assert mock_unit_data == {"departing": "True"}


def test_on_async_relation_joined():
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_unit_data = {}
    mock_charm.unit_peer_data = mock_unit_data

    mock_charm._unit_ip = "10.0.0.1"

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_highest_promoted_cluster_counter_value = MagicMock(return_value="1")

    result = relation._on_async_relation_joined(mock_event)

    assert result is None

    assert mock_unit_data == {"unit-promoted-cluster-counter": "1"}

    relation._get_highest_promoted_cluster_counter_value.assert_called_once()


def test_on_create_replication():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_application = MagicMock(spec=Application)
    relation._get_primary_cluster = MagicMock(return_value=mock_application)

    result = relation._on_create_replication(mock_event)

    assert result is None
    mock_event.fail.assert_called_once_with("There is already a replication set up.")

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_primary_cluster = MagicMock(return_value=None)

    mock_relation = MagicMock()
    mock_relation.name = REPLICATION_CONSUMER_RELATION
    type(relation)._relation = PropertyMock(return_value=mock_relation)

    result = relation._on_create_replication(mock_event)

    assert result is None
    mock_event.fail.assert_called_once_with(
        "This action must be run in the cluster where the offer was created."
    )
    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_primary_cluster = MagicMock(return_value=None)

    relation._handle_replication_change = MagicMock(return_value=True)

    mock_relation = MagicMock()
    mock_relation.name = "Something"
    type(relation)._relation = PropertyMock(return_value=mock_relation)

    result = relation._on_create_replication(mock_event)

    assert result is None

    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._get_primary_cluster = MagicMock(return_value=None)

    relation._handle_replication_change = MagicMock(return_value=False)

    mock_relation = MagicMock()
    mock_relation.name = "Something"
    type(relation)._relation = PropertyMock(return_value=mock_relation)

    result = relation._on_create_replication(mock_event)

    assert result is None


def test_promote_to_primary():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()
    mock_relation.status = MagicMock()
    mock_relation.status.message = "Something"

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._get_primary_cluster = MagicMock(return_value=None)

    type(relation).app = PropertyMock(return_value=mock_relation)
    result = relation.promote_to_primary(mock_event)
    assert result is None

    mock_event.fail.assert_called_once_with(
        "No primary cluster found. Run `create-replication` action in the cluster where the offer was created."
    )

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()
    mock_app = MagicMock(spec=Application)
    mock_relation.status = MagicMock()
    mock_relation.status.message = READ_ONLY_MODE_BLOCKING_MESSAGE

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._get_primary_cluster = MagicMock(return_value=None)

    type(relation).app = PropertyMock(return_value=mock_app)
    relation._handle_replication_change = MagicMock(return_value=False)

    result = relation.promote_to_primary(mock_event)

    assert result is None


def test__configure_standby_cluster():
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = MagicMock()
    relation._relation.name = REPLICATION_CONSUMER_RELATION
    relation._update_internal_secret = MagicMock(return_value=False)

    result = relation._configure_standby_cluster(mock_event)

    assert result is False

    mock_event.defer.assert_called_once()

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = MagicMock()
    relation._relation.name = "something_else"
    relation._update_internal_secret = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=(None, 2))

    with pytest.raises(Exception) as exc_info:
        relation._configure_standby_cluster(mock_event)

    assert str(exc_info.value) == "2"

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._relation = MagicMock()
    relation._relation.name = "some_relation"
    relation._relation.app = "remote-app"
    relation._relation.data = {relation._relation.app: {"system-id": "123"}}

    relation._update_internal_secret = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=("456", None))
    relation.charm = MagicMock()
    relation.charm.app_peer_data = {}

    with patch("subprocess.check_call") as mock_check_call:
        result = relation._configure_standby_cluster(mock_event)

        assert result is True
        mock_check_call.assert_called_once()


def test_wait_for_standby_leader():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_charm.patroni_manager.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = False
    mock_charm.patroni_manager.is_member_isolated = True
    mock_charm.patroni_manager.restart_patroni = MagicMock()

    result = relation._wait_for_standby_leader(mock_event)
    assert result is True
    mock_charm.patroni_manager.restart_patroni.assert_called_once()
    mock_event.defer.assert_called_once()

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_charm.patroni_manager.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = False
    mock_charm.patroni_manageer.is_member_isolated = False

    result = relation._wait_for_standby_leader(mock_event)
    assert result is True
    mock_event.defer.assert_called_once()

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    mock_charm.patroni_manager.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = True

    result = relation._wait_for_standby_leader(mock_event)
    assert result is False


def test_get_partner_addresses():
    mock_charm = MagicMock()

    mock_charm._peer_members_ips = ["str"]
    mock_charm.app = MagicMock()
    mock_charm.unit = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm._peers = MagicMock()
    mock_charm._peers.data = {mock_charm.unit: {}}

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._get_primary_cluster = MagicMock(return_value=None)
    relation._get_highest_promoted_cluster_counter_value = MagicMock(return_value=None)

    result = relation.get_partner_addresses()

    assert result == ["str"]


def test_handle_replication_change():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._can_promote_cluster = MagicMock(return_value=False)
    result = relation._handle_replication_change(mock_event)
    assert result is False

    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()
    mock_relation.units = []

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._can_promote_cluster = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock()
    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=mock_relation,
    ):
        result = relation._handle_replication_change(mock_event)

    assert result is False
    relation.get_system_identifier.assert_not_called()
    mock_event.fail.assert_called_once_with(
        "All units from the other cluster must publish their unit addresses in the relation data."
    )

    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()
    mock_unit = MagicMock()
    mock_unit.app = mock_relation.app
    mock_relation.units = [mock_unit]
    mock_relation.data = {mock_unit: {"unit-address": "10.0.0.1"}, mock_charm.app: {}}

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._can_promote_cluster = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=(12345, "some error"))
    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=mock_relation,
    ):
        result = relation._handle_replication_change(mock_event)

    assert result is False

    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_relation = MagicMock()

    mock_unit1 = MagicMock()
    mock_unit2 = MagicMock()
    mock_unit1.app = mock_relation.app
    mock_unit2.app = mock_relation.app
    mock_relation.units = [mock_unit1, mock_unit2]
    mock_relation.data = {
        mock_unit1: {"unit-address": "10.0.0.1"},
        mock_unit2: {"unit-address": "10.0.0.2"},
        mock_charm.app: {},
    }

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._can_promote_cluster = MagicMock(return_value=True)
    relation.get_system_identifier = MagicMock(return_value=(12345, None))
    relation._get_highest_promoted_cluster_counter_value = MagicMock(return_value="1")
    relation._update_primary_cluster_data = MagicMock()

    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=mock_relation,
    ):
        result = relation._handle_replication_change(mock_event)

    assert result is True
    relation._can_promote_cluster.assert_called_once_with(mock_event)
    relation.get_system_identifier.assert_called_once()
    relation._get_highest_promoted_cluster_counter_value.assert_called_once()
    relation._update_primary_cluster_data.assert_called_once_with(2, 12345)
    mock_event.fail.assert_not_called()


def test_re_emit_async_relation_changed_event():
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)
    mock_relation = MagicMock()
    mock_relation.name = "replication-offer"
    mock_relation.app = MagicMock()
    mock_relation.units = []

    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=mock_relation,
    ):
        relation._re_emit_async_relation_changed_event()

    mock_charm.on.replication_offer_relation_changed.emit.assert_not_called()

    remote_unit = MagicMock()
    remote_unit.app = mock_relation.app
    mock_relation.units = [remote_unit]
    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=mock_relation,
    ):
        relation._re_emit_async_relation_changed_event()

    mock_charm.on.replication_offer_relation_changed.emit.assert_called_once_with(
        mock_relation,
        app=mock_relation.app,
        unit=remote_unit,
    )


def test_handle_forceful_promotion():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = True
    relation = PostgreSQLAsyncReplication(mock_charm)
    result = relation._handle_forceful_promotion(mock_event)

    assert result is True
    # 2.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = False

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._relation = MagicMock()
    relation._relation.app = MagicMock()
    relation._relation.app.name = "test-app"

    relation.get_all_primary_cluster_endpoints = MagicMock(return_value=[1, 2, 3])

    mock_charm.patroni_manager.get_primary.side_effect = RetryError("timeout")

    result = relation._handle_forceful_promotion(mock_event)

    mock_event.fail.assert_called_once_with(
        "test-app isn't reachable. Pass `force=true` to promote anyway."
    )
    assert result is False
    # 3.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = False

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._relation = MagicMock()
    relation._relation.app = MagicMock()
    relation._relation.app.name = "test-app"

    relation.get_all_primary_cluster_endpoints = MagicMock(return_value=[1, 2, 3])

    mock_charm._patroni.get_primary.side_effect = None

    result = relation._handle_forceful_promotion(mock_event)

    assert result is True
    # 4.
    mock_charm = MagicMock()
    mock_event = MagicMock()

    mock_event.params.get.return_value = False

    relation = PostgreSQLAsyncReplication(mock_charm)

    relation._relation = MagicMock()
    relation._relation.app = MagicMock()
    relation._relation.app.name = "test-app"

    relation.get_all_primary_cluster_endpoints = MagicMock(return_value=[])

    mock_charm._patroni.get_primary.side_effect = None

    result = relation._handle_forceful_promotion(mock_event)

    assert result is True


def test_on_async_relation_broken():
    # 1.
    mock_charm = MagicMock()
    mock_event = MagicMock()
    mock_charm._peers = True

    relation = PostgreSQLAsyncReplication(mock_charm)

    result = relation._on_async_relation_broken(mock_event)

    assert result is None
    # 2.
    mock_charm = MagicMock()
    mock_charm._peers = MagicMock()
    mock_charm.is_unit_departing = False
    mock_charm.patroni_manager.get_standby_leader.return_value = None
    mock_charm.unit.is_leader.return_value = True
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._on_async_relation_broken(mock_event)

    assert mock_charm.update_config.called

    # 3. get_standby_leader raises (transient teardown failure, e.g. network-get during a dead-DC
    # force-removal): the hook must NOT crash and must still clear the counter, so the unit does
    # not wedge in error (DPE-10203 / Issue B).
    mock_charm = MagicMock()
    mock_charm._peers = MagicMock()
    mock_charm.is_unit_departing = False
    mock_charm.patroni_manager.get_standby_leader.side_effect = Exception(
        "network-get exited status 1"
    )
    mock_charm.unit.is_leader.return_value = True
    mock_charm.app_peer_data = {"promoted-cluster-counter": "2"}
    mock_event = MagicMock()

    relation = PostgreSQLAsyncReplication(mock_charm)
    relation._on_async_relation_broken(mock_event)  # must not raise

    assert mock_charm.app_peer_data.get("promoted-cluster-counter") == ""


def test_clear_stale_promotion():
    # Leader, no async relation, positive counter -> cleared + config re-rendered.
    mock_charm = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.app_peer_data = {"promoted-cluster-counter": "2"}
    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication, "_relation", new_callable=PropertyMock, return_value=None
    ):
        relation.clear_stale_promotion()
    assert mock_charm.app_peer_data.get("promoted-cluster-counter") == ""
    mock_charm.update_config.assert_called_once()

    # An async relation exists -> no-op (the counter is managed by the relation lifecycle).
    mock_charm = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm._patroni.get_standby_leader.return_value = None
    mock_charm.app_peer_data = {"promoted-cluster-counter": "2"}
    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=MagicMock(),
    ):
        relation.clear_stale_promotion()
    assert mock_charm.app_peer_data.get("promoted-cluster-counter") == "2"
    mock_charm.update_config.assert_not_called()

    # Non-leader -> no-op.
    mock_charm = MagicMock()
    mock_charm.unit.is_leader.return_value = False
    mock_charm.app_peer_data = {"promoted-cluster-counter": "2"}
    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication, "_relation", new_callable=PropertyMock, return_value=None
    ):
        relation.clear_stale_promotion()
    assert mock_charm.app_peer_data.get("promoted-cluster-counter") == "2"

    # Counter "0" (a standby already in read-only mode) -> left untouched, no Patroni call needed.
    mock_charm = MagicMock()
    mock_charm.unit.is_leader.return_value = True
    mock_charm.app_peer_data = {"promoted-cluster-counter": "0"}
    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication, "_relation", new_callable=PropertyMock, return_value=None
    ):
        relation.clear_stale_promotion()
    assert mock_charm.app_peer_data.get("promoted-cluster-counter") == "0"
    mock_charm.update_config.assert_not_called()
    mock_charm._patroni.get_standby_leader.assert_not_called()


def test_get_secret_creates_owned_secret_under_offer_label():
    # Regression for DPE-10203: the offer/primary side must own the shared secret under a label
    # distinct from the consumer alias (SECRET_LABEL). A former standby keeps a stale SECRET_LABEL
    # alias that Juju leaves reserved after the remote secret is gone, so owning under SECRET_LABEL
    # would deadlock with "secret with label already exists" on the next create-replication.
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    app_secret = MagicMock()
    app_secret.peek_content.return_value = {
        "operator-password": "op",
        "replication-password": "rep",
        "system-id": "x",
    }

    # First get_secret: the peer app secret (content source). Second: the offer-label lookup,
    # which is absent on a former standby -> triggers creation.
    mock_charm.model.get_secret.side_effect = [app_secret, SecretNotFoundError()]
    mock_charm.unit.is_leader.return_value = True

    result = relation._get_secret()

    # Owned secret is created under the offer-specific label, never the bare consumer alias.
    mock_charm.model.app.add_secret.assert_called_once()
    _, kwargs = mock_charm.model.app.add_secret.call_args
    assert kwargs["label"] == OFFER_SECRET_LABEL
    assert kwargs["label"] != SECRET_LABEL
    # Only password fields are shared between clusters.
    assert kwargs["content"] == {"operator-password": "op", "replication-password": "rep"}
    assert result is mock_charm.model.app.add_secret.return_value


def test_get_secret_reuses_existing_offer_secret():
    # When the owned secret already exists under the offer label, reuse it (look it up by the
    # offer label) instead of creating a new one; only rewrite content when it drifts.
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    app_secret = MagicMock()
    app_secret.peek_content.return_value = {"operator-password": "op"}

    existing = MagicMock()
    existing.id = "secret://uuid/abc"
    existing.peek_content.return_value = {"operator-password": "op"}

    mock_charm.model.get_secret.side_effect = [app_secret, existing]

    result = relation._get_secret()

    mock_charm.model.app.add_secret.assert_not_called()
    existing.set_content.assert_not_called()
    assert result is existing
    assert any(
        call.kwargs.get("label") == OFFER_SECRET_LABEL
        for call in mock_charm.model.get_secret.call_args_list
    )


def test__get_primary_cluster_skips_unreadable_dead_peer_databag():
    # DPE-10203: after a dead-DC teardown the remote app's databag on the dying
    # cross-model async relation is unreadable — `relation-get --app <remote>`
    # returns "permission denied" (surfaced as ModelError) once the offering DC is
    # gone. _get_primary_cluster must skip that peer instead of crashing every hook,
    # so the readable local peer is still evaluated.
    mock_charm = MagicMock()
    local_app = MagicMock()
    mock_charm.app = local_app

    remote_app = MagicMock()
    dead_databag = MagicMock()
    dead_databag.get.side_effect = ModelError("ERROR permission denied")
    offer_relation = MagicMock()
    offer_relation.app = remote_app
    offer_relation.data = {remote_app: dead_databag}

    local_databag = MagicMock()
    local_databag.get.return_value = "1"
    mock_charm.all_peer_data = {local_app: local_databag}

    mock_model = MagicMock()
    mock_model.get_relation.side_effect = [offer_relation, None]

    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication, "model", new_callable=PropertyMock, return_value=mock_model
    ):
        # Must not raise ModelError; the unreadable dead peer is skipped and the
        # readable local peer (counter "1") is selected as the primary.
        assert relation._get_primary_cluster() is local_app
    dead_databag.get.assert_called_once_with("promoted-cluster-counter", "0")


def test__relation_skips_unreadable_dying_relation(monkeypatch):
    # DPE-10203: a dead-DC teardown leaves the cross-model async relation in a
    # dying state whose databags raise ModelError ("permission denied") on any
    # read, even though get_relation still returns it. _relation must probe and
    # treat such a relation as absent, so the promoted primary reconciles as a
    # standalone cluster instead of crashing every hook that writes relation data.
    # Restore the real property for this test's scope (an earlier test may have
    # leaked a class-level PropertyMock over it); monkeypatch reverts it afterwards.
    monkeypatch.setattr(PostgreSQLAsyncReplication, "_relation", _REAL_RELATION_PROPERTY)
    mock_charm = MagicMock()

    dying = MagicMock()
    dying_databag = MagicMock()
    dying_databag.get.side_effect = ModelError("ERROR permission denied")
    dying.data.__getitem__.return_value = dying_databag

    readable = MagicMock()  # its databag read succeeds (default MagicMock, no raise)

    relation = PostgreSQLAsyncReplication(mock_charm)
    # First candidate (offer) is the dying relation; second (consumer) is readable.
    relation.model.get_relation.side_effect = [dying, readable]

    # The dying relation is skipped (probe raised); the readable one is returned.
    assert relation._relation is readable
    dying_databag.get.assert_called_once_with("unit-address")


# --- DPE-10203 follow-up: consumer reads the shared secret by id, never by label -------------
# The consumer used to fetch the offer secret with ``get_secret(id=..., label=SECRET_LABEL)``,
# registering a local consumer alias that Juju leaves reserved after a dead-DC teardown. Matching
# MySQL's async-replication design, the consumer now references the secret purely by the id
# published in relation data, so no alias can go stale. These tests pin that behaviour.


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("secret://uuid/abc123", "secret://uuid/abc123", True),
        ("secret://uuid/abc123", "secret:abc123", True),  # format-insensitive
        ("secret:abc123", "secret://uuid/abc123", True),
        ("secret://uuid/abc123", "secret://uuid/xyz789", False),
        (None, "secret:abc123", False),
        ("secret:abc123", None, False),
        (None, None, False),
    ],
)
def test_same_secret_id(a, b, expected):
    assert _same_secret_id(a, b) is expected


def _consumer_relation(secret_id):
    relation = MagicMock()
    relation.name = REPLICATION_CONSUMER_RELATION
    relation.app = "primary-app"
    relation.data = {"primary-app": {"primary-cluster-data": json.dumps({"secret-id": secret_id})}}
    return relation


def test_update_internal_secret_reads_by_id_without_label():
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    secret = MagicMock()
    secret.peek_content.return_value = {"operator-password": "pw"}
    mock_charm.model.get_secret.return_value = secret

    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=_consumer_relation("secret://uuid/abc123"),
    ):
        assert relation._update_internal_secret() is True

    # Fetched purely by id, with no ``label=`` alias registered.
    mock_charm.model.get_secret.assert_called_once_with(id="secret://uuid/abc123")


def test_update_internal_secret_returns_false_without_secret_id():
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    with patch.object(
        PostgreSQLAsyncReplication,
        "_relation",
        new_callable=PropertyMock,
        return_value=_consumer_relation(None),
    ):
        assert relation._update_internal_secret() is False

    mock_charm.model.get_secret.assert_not_called()


def test_on_secret_changed_consumer_matches_by_id_not_label():
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_event = MagicMock()
    mock_event.secret.id = "secret://uuid/abc123"  # same key, different URI format
    mock_event.secret.label = None  # no alias any more

    with (
        patch.object(
            PostgreSQLAsyncReplication,
            "_relation",
            new_callable=PropertyMock,
            return_value=_consumer_relation("secret:abc123"),
        ),
        patch.object(
            PostgreSQLAsyncReplication, "_update_internal_secret", return_value=True
        ) as mock_update,
    ):
        relation._on_secret_changed(mock_event)

    mock_update.assert_called_once()
    mock_event.defer.assert_not_called()


def test_on_secret_changed_consumer_ignores_unrelated_secret():
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_event = MagicMock()
    mock_event.secret.id = "secret://uuid/DIFFERENT"
    mock_event.secret.label = SECRET_LABEL  # a legacy label must NOT trigger the sync anymore

    with (
        patch.object(
            PostgreSQLAsyncReplication,
            "_relation",
            new_callable=PropertyMock,
            return_value=_consumer_relation("secret:abc123"),
        ),
        patch.object(
            PostgreSQLAsyncReplication, "_update_internal_secret", return_value=True
        ) as mock_update,
    ):
        relation._on_secret_changed(mock_event)

    mock_update.assert_not_called()
    mock_event.defer.assert_not_called()


def test_on_secret_changed_consumer_defers_when_secret_not_ready():
    mock_charm = MagicMock()
    relation = PostgreSQLAsyncReplication(mock_charm)

    mock_event = MagicMock()
    mock_event.secret.id = "secret://uuid/abc123"
    mock_event.secret.label = None

    with (
        patch.object(
            PostgreSQLAsyncReplication,
            "_relation",
            new_callable=PropertyMock,
            return_value=_consumer_relation("secret:abc123"),
        ),
        patch.object(PostgreSQLAsyncReplication, "_update_internal_secret", return_value=False),
    ):
        relation._on_secret_changed(mock_event)

    mock_event.defer.assert_called_once()


def test__get_highest_promoted_cluster_counter_value_skips_unreadable_dead_peer():
    # DPE-10203: after a dead-DC teardown the remote app's databag on the dying
    # cross-model async relation is unreadable — `relation-get --app <remote>`
    # raises ModelError ("permission denied") once the offering DC is gone. Like
    # _get_primary_cluster, _get_highest_promoted_cluster_counter_value must skip
    # that peer instead of crashing the hook (it crashed replication-offer-relation
    # -joined on the promoted cluster, blocking re-replication), still honouring the
    # readable local peer counter.
    mock_charm = MagicMock()

    remote_app = MagicMock()
    dead_databag = MagicMock()
    dead_databag.get.side_effect = ModelError("ERROR permission denied")
    offer_relation = MagicMock()
    offer_relation.app = remote_app
    offer_relation.data = {remote_app: dead_databag}

    # The local peer databag is readable and holds a higher counter.
    mock_charm.app_peer_data = {"promoted-cluster-counter": "3"}

    mock_model = MagicMock()
    mock_model.get_relation.side_effect = [offer_relation, None]

    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication, "model", new_callable=PropertyMock, return_value=mock_model
    ):
        # Must not raise; the unreadable dead peer is skipped and the local counter wins.
        assert relation._get_highest_promoted_cluster_counter_value() == "3"
    dead_databag.get.assert_called_once_with("promoted-cluster-counter", "0")


# --- DPE-10203 dead-DC hardening: async-relation reads must survive an unreadable peer ---------


def test_safe_databag_get_returns_value_when_readable():
    assert _safe_databag_get({"k": "v"}, "k") == "v"
    assert _safe_databag_get({}, "k", "default") == "default"


def test_safe_databag_get_treats_unreadable_databag_as_absent():
    # A dead-DC teardown makes the remote databag raise ModelError on any read; callers
    # must see the key as absent instead of crashing the hook (DPE-10203).
    dead_databag = MagicMock()
    dead_databag.get.side_effect = ModelError("ERROR permission denied")
    assert _safe_databag_get(dead_databag, "k") is None
    assert _safe_databag_get(dead_databag, "k", "default") == "default"


def test_remote_unit_addresses_skips_unreadable_dead_peer_units():
    # The dying cross-model relation's unit databags raise ModelError on read;
    # _remote_unit_addresses must skip them and still return the readable addresses.
    mock_charm = MagicMock()

    good_unit = MagicMock()
    offer_relation = MagicMock()
    offer_relation.units = [good_unit]
    offer_relation.data = {good_unit: {"unit-address": "10.0.0.1"}}

    dead_unit = MagicMock()
    dead_databag = MagicMock()
    dead_databag.get.side_effect = ModelError("ERROR permission denied")
    dead_relation = MagicMock()
    dead_relation.units = [dead_unit]
    dead_relation.data = {dead_unit: dead_databag}

    mock_model = MagicMock()
    mock_model.get_relation.side_effect = [offer_relation, dead_relation]

    relation = PostgreSQLAsyncReplication(mock_charm)
    with patch.object(
        PostgreSQLAsyncReplication, "model", new_callable=PropertyMock, return_value=mock_model
    ):
        assert relation._remote_unit_addresses() == ["10.0.0.1"]
