#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base class for charm relations."""

import contextlib
import enum
import json
import logging
from typing import Any

from ops.model import Application, Relation, Unit

from single_kernel_postgresql.lib.charms.data_platform_libs.v0.data_interfaces import Data

logger = logging.getLogger(__name__)


class RelationState:
    """Relation state object."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: Data,
        component: Unit | Application | None,
    ):
        self.relation = relation
        self.data_interface = data_interface
        self.unit = component
        self.relation_data = self.data_interface.as_dict(self.relation.id) if self.relation else {}

    def __bool__(self) -> bool:
        """Boolean evaluation based on the existence of self.relation."""
        try:
            return bool(self.relation)
        except AttributeError:
            return False

    def update(self, items: dict[str, str]) -> None:
        """Write to relation data."""
        if not self.relation:
            logger.warning(
                f"Fields {list(items.keys())} were attempted to be written on the relation before it exists."
            )
            return

        delete_fields = [key for key in items if not items[key]]
        update_content = {k: items[k] for k in items if k not in delete_fields}

        self.relation_data.update(update_content)

        for field in delete_fields:
            if field not in self.relation_data:
                logger.debug(
                    f"Field '{field}' not found in relation data for deletion. Skipping deletion for this field."
                )
            else:
                with contextlib.suppress(KeyError):
                    del self.relation_data[field]

    def get_object(self, key: str) -> dict[str, Any] | None:
        """Get dict / json object from the relation data store."""
        return json.loads(data) if (data := self.relation_data.get(key)) is not None else None

    def put_object(self, key: str, value: dict[str, Any], merge: bool = False) -> None:
        """Put dict / json object into relation data store."""
        if merge and (stored := self.get_object(key)) is not None:
            stored.update(value)
            value = stored

        sorted_value = self.sort_payload(value)

        payload_str = None
        if value is not None:
            payload_str = json.dumps(
                sorted_value, default=RelationState._default_encoder, sort_keys=True
            )

        self.update({key: payload_str})

    def sort_payload(self, payload: Any) -> Any:
        """Sort input payloads to avoid rel-changed events for same unordered objects."""
        if isinstance(payload, dict):
            # Sort dictionary by keys
            return {key: self.sort_payload(value) for key, value in sorted(payload.items())}
        elif isinstance(payload, list):
            # Sort each item in the list and then sort the list
            sorted_list = [self.sort_payload(item) for item in payload]
            try:
                return sorted(sorted_list)
            except TypeError:
                # If items are not sortable, return as is
                return sorted_list
        else:
            # Return the value as is for non-dict, non-list types
            return payload

    @staticmethod
    def _default_encoder(o: Any) -> Any:
        """Default encoder for json dumps."""
        if isinstance(o, enum.Enum):
            return o.value

        if hasattr(o, "__dict__"):
            return vars(o)

        raise TypeError(f"Unserializable {o.__class__.__name__}")
