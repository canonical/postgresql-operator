import json
import logging

from ops import ActionEvent, Object, Relation, RelationChangedEvent, RelationJoinedEvent

from constants import APP_SCOPE, REPLICATION_PASSWORD_KEY, REPLICATION_USER

logger = logging.getLogger(__name__)

LOGICAL_REPLICATION_OFFER_RELATION = "logical-replication-offer"
LOGICAL_REPLICATION_RELATION = "logical-replication"

class PostgreSQLLogicalReplication(Object):
    def __init__(self, charm):
        super().__init__(charm, "postgresql_logical_replication")
        self.charm = charm
        # Relations
        self.charm.framework.observe(self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_joined,
                                     self._on_offer_relation_joined)
        self.charm.framework.observe(self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_changed,
                                     self._on_offer_relation_changed)
        # Actions
        self.charm.framework.observe(self.charm.on.add_publication_action, self._on_add_publication)
        self.charm.framework.observe(self.charm.on.list_publications_action, self._on_list_publications)
        self.charm.framework.observe(self.charm.on.remove_publication_action, self._on_remove_publication)
        self.charm.framework.observe(self.charm.on.subscribe_action, self._on_subscribe)
        self.charm.framework.observe(self.charm.on.list_subscriptions_action, self._on_list_subscriptions)
        self.charm.framework.observe(self.charm.on.unsubscribe_action, self._on_unsubscribe)

#region Relations

    def _on_offer_relation_joined(self, event: RelationJoinedEvent):
        if not self.charm.unit.is_leader():
            return

        if not self.charm.primary_endpoint:
            event.defer()
            logger.debug(f"{LOGICAL_REPLICATION_OFFER_RELATION}: joined event deferred as primary is unavailable right now")
            return

        # TODO: add primary change check
        # TODO: replication-user-secret
        event.relation.data[self.model.app].update({
            "publications": self.charm.app_peer_data.get("publications", ""),
            "replication-user": REPLICATION_USER,
            "replication-user-secret": self.charm.get_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY),
            "primary": self.charm.primary_endpoint
        })

    def _on_offer_relation_changed(self, event: RelationChangedEvent):
        if not self.charm.unit.is_leader():
            return

        subscriptions_str = event.relation.data[event.app].get("subscriptions", "")
        subscriptions = subscriptions_str.split(",") if subscriptions_str else ()
        replication_slots = self._get_relation_replication_slots(event.relation)

        for subscription in subscriptions:
            if subscription not in replication_slots:
                # TODO: validation on publication existence
                self._add_replication_slot(event.relation, subscription)

        for publication in replication_slots:
            if publication not in subscriptions:
                self._remove_replication_slot(event.relation, publication)

#endregion

#region Actions

    def _on_add_publication(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Publications management can be done only on the leader unit")
            return
        if not (publication_name := event.params.get("name")):
            event.fail("name parameter is required")
            return
        if not (publication_db := event.params.get("database")):
            event.fail("database parameter is required")
            return
        if not (publication_tables := event.params.get("tables")):
            event.fail("tables parameter is required")
            return
        publications = self._get_publications_from_str(self.charm.app_peer_data.get("publications"))
        if publication_name in publications:
            event.fail("Such publication already exists")
            return
        # TODO: check on schema existence
        publications[publication_name] = {
            "database": publication_db,
            "tables": publication_tables.split(",")
        }
        self._set_publications(publications)

    def _on_list_publications(self, event: ActionEvent):
        # TODO: table formatting
        if not self.charm.unit.is_leader():
            event.fail("Publications management can be done only on the leader unit")
            return
        event.set_results({
            "publications": self.charm.app_peer_data.get("publications", "{}")
        })

    def _on_remove_publication(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Publications management can be done only on the leader unit")
            return
        if not (publication_name := event.params.get("name")):
            event.fail("name parameter is required")
            return
        # TODO: validate to delete only unused publications
        publications = self._get_publications_from_str(self.charm.app_peer_data.get("publications"))
        if publication_name not in publications:
            event.fail("No such publication")
            return
        del publications[publication_name]
        self._set_publications(publications)

    def _on_subscribe(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Subscriptions management can be done only on the leader unit")
            return
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            event.fail("Subscription management can be done only with an active logical replication connection")
            return
        if not (publication_name := event.params.get("name")):
            event.fail("name parameter is required")
            return
        subscriptions = self._get_str_list(relation.data[self.model.app].get("subscriptions"))
        if publication_name in subscriptions:
            event.fail("Such subscription already exists")
            return
        publications = self._get_publications_from_str(relation.data[relation.app].get("publications"))
        # TODO: validation on overlaps and existing scheme
        if publication_name not in publications:
            event.fail("No such publication offered")
            return
        subscriptions.append(publication_name)
        relation.data[self.model.app]["subscriptions"] = ",".join(subscriptions)

    def _on_list_subscriptions(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Subscriptions management can be done only on the leader unit")
            return
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            event.fail("Subscription management can be done only with an active logical replication connection")
            return
        # TODO: table formatting
        event.set_results({
            "subscriptions": relation.data[self.model.app].get("subscriptions", "")
        })

    def _on_unsubscribe(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Subscriptions management can be done only on the leader unit")
            return
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            event.fail("Subscription management can be done only with an active logical replication connection")
            return
        if not (subscription_name := event.params.get("name")):
            event.fail("name parameter is required")
            return
        subscriptions = self._get_str_list(relation.data[self.model.app].get("subscriptions"))
        if subscription_name not in subscriptions:
            event.fail("No such subscription")
            return
        relation.data[self.model.app]["subscriptions"] = ",".join([
            x
            for x in self._get_str_list(relation.data[self.model.app].get("subscriptions"))
            if x != subscription_name
        ])
        # TODO: unsubscribe

#endregion

    @staticmethod
    def _get_publications_from_str(publications_str: str | None) -> dict[str, dict[str, any]]:
        return json.loads(publications_str or "{}")

    def _set_publications(self, publications: dict[str, dict[str, any]]):
        publications_str = json.dumps(publications)
        self.charm.app_peer_data["publications"] = publications_str
        for rel in self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ()):
            rel.data[self.model.app]["publications"] = publications_str

    def _get_relation_replication_slots(self, relation: Relation) -> dict[str, str]:
        return json.loads(relation.data[self.model.app].get("replication-slots", "{}"))

    @staticmethod
    def _get_str_list(list_str: str | None) -> list[str]:
        return list_str.split(",") if list_str else []

    def _add_replication_slot(self, relation: Relation, publication: str):
        # TODO: overwrite check
        relation_replication_slots = self._get_relation_replication_slots(relation)
        global_replication_slots = self._get_str_list(self.charm.app_peer_data.get("replication-slots"))

        # TODO: replication slot random name
        new_replication_slot_name = publication

        global_replication_slots.append(new_replication_slot_name)
        self.charm.app_peer_data["replication-slots"] = ",".join(global_replication_slots)
        relation_replication_slots[publication] = new_replication_slot_name
        relation.data[self.model.app]["replication-slots"] = json.dumps(relation_replication_slots)

        # TODO: patroni config update

    def _remove_replication_slot(self, relation: Relation, publication: str):
        relation_replication_slots = self._get_relation_replication_slots(relation)
        global_replication_slots = self._get_str_list(self.charm.app_peer_data.get("replication-slots"))
        replication_slot_name = relation_replication_slots[publication]
        global_replication_slots.remove(replication_slot_name)
        self.charm.app_peer_data["replication-slots"] = ",".join(global_replication_slots)
        del relation_replication_slots[publication]
        relation.data[self.model.app]["replication-slots"] = json.dumps(relation_replication_slots)
        # TODO: patroni config update
