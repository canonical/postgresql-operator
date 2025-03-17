# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Logical Replication implementation.

TODO: add description after specification is accepted.
"""

import json
import logging

from ops import (
    ActionEvent,
    Object,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
)

from constants import (
    APP_SCOPE,
    USER,
    USER_PASSWORD_KEY,
)

logger = logging.getLogger(__name__)

LOGICAL_REPLICATION_OFFER_RELATION = "logical-replication-offer"
LOGICAL_REPLICATION_RELATION = "logical-replication"


class PostgreSQLLogicalReplication(Object):
    """Defines the logical-replication logic."""

    def __init__(self, charm):
        super().__init__(charm, "postgresql_logical_replication")
        self.charm = charm
        # Relations
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_joined,
            self._on_offer_relation_joined,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_changed,
            self._on_offer_relation_changed,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_broken,
            self._on_offer_relation_broken,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_changed, self._on_relation_changed
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_departed,
            self._on_relation_departed,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_broken, self._on_relation_broken
        )
        # Actions
        self.charm.framework.observe(
            self.charm.on.add_publication_action, self._on_add_publication
        )
        self.charm.framework.observe(
            self.charm.on.list_publications_action, self._on_list_publications
        )
        self.charm.framework.observe(
            self.charm.on.remove_publication_action, self._on_remove_publication
        )
        self.charm.framework.observe(self.charm.on.subscribe_action, self._on_subscribe)
        self.charm.framework.observe(
            self.charm.on.list_subscriptions_action, self._on_list_subscriptions
        )
        self.charm.framework.observe(self.charm.on.unsubscribe_action, self._on_unsubscribe)

    # region Relations

    def _on_offer_relation_joined(self, event: RelationJoinedEvent):
        if not self.charm.unit.is_leader():
            return
        if not self.charm.primary_endpoint:
            event.defer()
            logger.debug(
                f"{LOGICAL_REPLICATION_OFFER_RELATION}: joined event deferred as primary is unavailable right now"
            )
            return

        # TODO: add primary change check
        # TODO: replication-user-secret
        event.relation.data[self.model.app].update({
            "publications": self.charm.app_peer_data.get("publications", ""),
            # "replication-user": REPLICATION_USER,
            # "replication-user-secret": self.charm.get_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY),
            "replication-user": USER,
            "replication-user-secret": self.charm.get_secret(APP_SCOPE, USER_PASSWORD_KEY),
            "primary": self.charm.primary_endpoint,
        })

    def _on_offer_relation_changed(self, event: RelationChangedEvent):
        if not self.charm.unit.is_leader():
            return

        subscriptions_str = event.relation.data[event.app].get("subscriptions", "")
        subscriptions = subscriptions_str.split(",") if subscriptions_str else ()
        publications = self._get_publications_from_str(
            self.charm.app_peer_data.get("publications")
        )
        relation_replication_slots = self._get_dict_from_str(
            event.relation.data[self.model.app].get("replication-slots")
        )
        global_replication_slots = self._get_dict_from_str(
            self.charm.app_peer_data.get("replication-slots")
        )

        for publication in subscriptions:
            if publication not in publications:
                logger.error(
                    f"Logical Replication: requested subscription for non-existing publication {publication}"
                )
                continue
            if publication not in relation_replication_slots:
                replication_slot_name = f"{event.relation.id}_{publication}"
                global_replication_slots[replication_slot_name] = publications[publication][
                    "database"
                ]
                relation_replication_slots[publication] = replication_slot_name
        for publication in relation_replication_slots.copy():
            if publication not in subscriptions:
                del global_replication_slots[relation_replication_slots[publication]]
                del relation_replication_slots[publication]

        self.charm.app_peer_data["replication-slots"] = json.dumps(global_replication_slots)
        event.relation.data[self.model.app]["replication-slots"] = json.dumps(
            relation_replication_slots
        )
        self.charm.update_config()

    def _on_offer_relation_departed(self, event: RelationDepartedEvent):
        if event.departing_unit == self.charm.unit and self.charm._peers is not None:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})

    def _on_offer_relation_broken(self, event: RelationBrokenEvent):
        if not self.charm._peers or self.charm.is_unit_departing:
            logger.debug(
                f"{LOGICAL_REPLICATION_OFFER_RELATION}: skipping departing unit in broken event"
            )
            return
        if not self.charm.unit.is_leader():
            return

        global_replication_slots = self._get_dict_from_str(
            self.charm.app_peer_data.get("replication-slots")
        )
        if len(global_replication_slots) == 0:
            return

        used_replication_slots = []
        for rel in self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ()):
            if rel == event.relation:
                continue
            used_replication_slots += [
                v
                for k, v in self._get_dict_from_str(
                    rel.data[self.model.app].get("replication-slots")
                ).items()
            ]

        deleting_replication_slots = [
            k for k, v in global_replication_slots.items() if k not in used_replication_slots
        ]
        for deleting_replication_slot in deleting_replication_slots:
            global_replication_slots.pop(deleting_replication_slot, None)
        self.charm.app_peer_data["replication-slots"] = json.dumps(global_replication_slots)
        self.charm.update_config()

    def _on_relation_changed(self, event: RelationChangedEvent):
        if not self._relation_changed_checks(event):
            return
        publications = self._get_publications_from_str(
            event.relation.data[event.app].get("publications")
        )
        replication_slots = self._get_dict_from_str(
            event.relation.data[event.app].get("replication-slots")
        )
        global_subscriptions = self._get_dict_from_str(
            self.charm.app_peer_data.get("subscriptions")
        )
        for subscription in self._get_str_list(
            event.relation.data[self.model.app].get("subscriptions")
        ):
            db = publications[subscription]["database"]
            if subscription in replication_slots and not self.charm.postgresql.subscription_exists(
                db, subscription
            ):
                self.charm.postgresql.create_subscription(
                    subscription,
                    event.relation.data[event.app]["primary"],
                    db,
                    event.relation.data[event.app]["replication-user"],
                    event.relation.data[event.app]["replication-user-secret"],
                    replication_slots[subscription],
                )
                global_subscriptions[subscription] = db
                self.charm.app_peer_data["subscriptions"] = json.dumps(global_subscriptions)

    def _on_relation_departed(self, event: RelationDepartedEvent):
        if event.departing_unit == self.charm.unit and self.charm._peers is not None:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})

    def _on_relation_broken(self, event: RelationBrokenEvent):
        if not self.charm._peers or self.charm.is_unit_departing:
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION}: skipping departing unit in broken event"
            )
            return
        if not self.charm.unit.is_leader():
            return
        if not self.charm.primary_endpoint:
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION}: broken event deferred as primary is unavailable right now"
            )
            event.defer()
            return False

        subscriptions = self._get_dict_from_str(self.charm.app_peer_data.get("subscriptions"))
        for subscription, db in subscriptions.copy().items():
            self.charm.postgresql.drop_subscription(db, subscription)
            del subscriptions[subscription]
            self.charm.app_peer_data["subscriptions"] = json.dumps(subscriptions)

    # endregion

    # region Actions

    def _on_add_publication(self, event: ActionEvent):
        # TODO: check on max replication slots
        if not self._add_publication_validation(event):
            return
        if not self.charm.postgresql.database_exists(event.params["database"]):
            event.fail(f"No such database {event.params['database']}")
            return
        for schematable in event.params["tables"].split(","):
            if len(schematable_split := schematable.split(".")) != 2:
                event.fail("All tables should be in schema.table format")
                return
            if not self.charm.postgresql.table_exists(
                event.params["database"], schematable_split[0], schematable_split[1]
            ):
                event.fail(f"No such table {schematable} in database {event.params['database']}")
                return
        publications = self._get_publications_from_str(
            self.charm.app_peer_data.get("publications")
        )
        publication_tables_split = event.params["tables"].split(",")
        self.charm.postgresql.create_publication(
            event.params["database"], event.params["name"], publication_tables_split
        )
        publications[event.params["name"]] = {
            "database": event.params["database"],
            "tables": publication_tables_split,
        }
        self._set_publications(publications)

    def _on_list_publications(self, event: ActionEvent):
        # TODO: table formatting
        if not self.charm.unit.is_leader():
            event.fail("Publications management can be done only on the leader unit")
            return
        event.set_results({"publications": self.charm.app_peer_data.get("publications", "{}")})

    def _on_remove_publication(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Publications management can be done only on the leader unit")
            return
        if not self.charm.primary_endpoint:
            event.fail("Publication management can be proceeded only with an active primary")
            return False
        if not (publication_name := event.params.get("name")):
            event.fail("name parameter is required")
            return
        publications = self._get_publications_from_str(
            self.charm.app_peer_data.get("publications")
        )
        if publication_name not in publications:
            event.fail("No such publication")
            return
        if self._count_publication_connections(publication_name):
            event.fail("Cannot remove publication while it's in use")
            return
        self.charm.postgresql.drop_publication(
            publications[publication_name]["database"], publication_name
        )
        del publications[publication_name]
        self._set_publications(publications)

    def _on_subscribe(self, event: ActionEvent):
        if not self._subscribe_validation(event):
            return
        relation = self.model.get_relation(LOGICAL_REPLICATION_RELATION)
        subscribing_publication = self._get_publications_from_str(
            relation.data[relation.app]["publications"]
        )[event.params["name"]]
        subscribing_database = subscribing_publication["database"]
        subscriptions = self._get_str_list(relation.data[self.model.app].get("subscriptions"))
        if not self.charm.postgresql.database_exists(subscribing_database):
            event.fail(f"No such database {subscribing_database}")
            return
        if self.charm.postgresql.subscription_exists(subscribing_database, event.params["name"]):
            event.fail(
                f"PostgreSQL subscription with conflicting name {event.params['name']} already exists in the database {subscribing_database}"
            )
            return
        for schematable in subscribing_publication["tables"]:
            schematable_split = schematable.split(".")
            if not self.charm.postgresql.table_exists(
                subscribing_database, schematable_split[0], schematable_split[1]
            ):
                event.fail(f"No such table {schematable} in database {subscribing_database}")
                return
            if not self.charm.postgresql.is_table_empty(
                subscribing_database, schematable_split[0], schematable_split[1]
            ):
                event.fail(
                    f"Table {schematable} in database {subscribing_database} should be empty before subscribing on it"
                )
                return
        subscriptions.append(event.params["name"])
        relation.data[self.model.app]["subscriptions"] = ",".join(subscriptions)

    def _on_list_subscriptions(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Subscriptions management can be done only on the leader unit")
            return
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            event.fail(
                "Subscription management can be done only with an active logical replication connection"
            )
            return
        # TODO: table formatting
        event.set_results({
            "subscriptions": relation.data[self.model.app].get("subscriptions", "")
        })

    def _on_unsubscribe(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            event.fail("Subscriptions management can be proceeded only on the leader unit")
            return
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            event.fail(
                "Subscription management can be proceeded only with an active logical replication connection"
            )
            return
        if not self.charm.primary_endpoint:
            event.fail("Subscription management can be proceeded only with an active primary")
            return False
        if not (subscription_name := event.params.get("name")):
            event.fail("name parameter is required")
            return
        subscriptions = self._get_str_list(relation.data[self.model.app].get("subscriptions"))
        if subscription_name not in subscriptions:
            event.fail("No such subscription")
            return
        self.charm.postgresql.drop_subscription(
            self._get_publications_from_str(relation.data[relation.app]["publications"])[
                subscription_name
            ]["database"],
            subscription_name,
        )
        subscriptions.remove(subscription_name)
        relation.data[self.model.app]["subscriptions"] = ",".join(subscriptions)

    # endregion

    def _relation_changed_checks(self, event: RelationChangedEvent) -> bool:
        if not self.charm.unit.is_leader():
            return False
        if not self.charm.primary_endpoint:
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION}: changed event deferred as primary is unavailable right now"
            )
            event.defer()
            return False
        if (
            not event.relation.data[event.app].get("primary")
            or not event.relation.data[event.app].get("replication-user")
            or not event.relation.data[event.app].get("replication-user-secret")
        ):
            logger.warning(
                f"{LOGICAL_REPLICATION_RELATION}: skipping changed event as there is no primary, replication-user or replication-user-secret in the remote application data"
            )
            return False
        return True

    def _add_publication_validation(self, event: ActionEvent) -> bool:
        if not self.charm.unit.is_leader():
            event.fail("Publications management can be proceeded only on the leader unit")
            return False
        if not self.charm.primary_endpoint:
            event.fail("Publication management can be proceeded only with an active primary")
            return False
        if not (publication_name := event.params.get("name")):
            event.fail("name parameter is required")
            return False
        if not event.params.get("database"):
            event.fail("database parameter is required")
            return False
        if not event.params.get("tables"):
            event.fail("tables parameter is required")
            return False
        if publication_name in self._get_publications_from_str(
            self.charm.app_peer_data.get("publications")
        ):
            event.fail("Such publication already exists")
            return False
        return True

    def _subscribe_validation(self, event: ActionEvent) -> bool:
        if not self.charm.unit.is_leader():
            event.fail("Subscriptions management can be proceeded only on the leader unit")
            return False
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            event.fail(
                "Subscription management can be proceeded only with an active logical replication connection"
            )
            return False
        if not self.charm.primary_endpoint:
            event.fail("Subscription management can be proceeded only with an active primary")
            return False
        if not (publication_name := event.params.get("name")):
            event.fail("name parameter is required")
            return False
        subscriptions = self._get_str_list(relation.data[self.model.app].get("subscriptions"))
        if publication_name in subscriptions:
            event.fail("Such subscription already exists")
            return False
        publications = self._get_publications_from_str(
            relation.data[relation.app].get("publications")
        )
        subscribing_publication = publications.get(publication_name)
        if not subscribing_publication:
            event.fail("No such publication offered")
            return False
        # Check overlaps with already subscribed publications
        if any(
            any(
                publication_table in subscribing_publication["tables"]
                for publication_table in publication_obj["tables"]
            )
            for (publication, publication_obj) in publications.items()
            if publication in subscriptions
            and publication_obj["database"] == subscribing_publication["database"]
        ):
            event.fail("Tables overlap detected with existing subscriptions")
            return False
        return True

    @staticmethod
    def _get_publications_from_str(
        publications_str: str | None = None,
    ) -> dict[str, dict[str, any]]:
        return json.loads(publications_str or "{}")

    def _set_publications(self, publications: dict[str, dict[str, any]]):
        publications_str = json.dumps(publications)
        self.charm.app_peer_data["publications"] = publications_str
        for rel in self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ()):
            rel.data[self.model.app]["publications"] = publications_str

    def _count_publication_connections(self, publication: str) -> int:
        count = 0
        for relation in self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ()):
            if publication in self._get_str_list(relation.data[relation.app].get("subscriptions")):
                count += 1
        return count

    @staticmethod
    def _get_dict_from_str(
        replication_slots_str: str | None = None,
    ) -> dict[str, str]:
        return json.loads(replication_slots_str or "{}")

    @staticmethod
    def _get_str_list(list_str: str | None = None) -> list[str]:
        return list_str.split(",") if list_str else []
