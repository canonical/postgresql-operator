# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""The coordinated ops is a class that ensures a certain activity is ran together.

The concept is similar to the "cohort" in snaps, where all units wait until they can
proceed to execute a certain activity, for example, restarting your service.

The process starts with the leader issuing a new coordination request. Effectively,
that is implemented as the _<relation-name>_coord_counter is increased +1 in the app level.
_<relation-name>_coord_approved is set to "False".

Each unit receives a relation-changed, which is then re-issued as a _coordinator_requested
event. Once the unit done its task, it should ack the request.
Each unit should ack the request by equaling its own _<relation-name>_coord_counter
to the app's value.

Once all units ack'ed the _<relation-name>_coord_counter, then the leader switches the
_<relation-name>_coord_approved to "True". All units then will process that new change as a
"coordinator-approved" event and execute the activity they have been waiting.

If there is a need to coordinate several activities in sequence, e.g. coordinated stop and then
coordinated start, it is recommended that the leader unit publishes twice a _requested, as follows:


    class MyCharm:

        def __init__(self, *args):
            self.stop_coordinator = CoordinatedOpsManager(relation, tag="_stop_my_charm")
            self.start_coordinator = CoordinatedOpsManager(relation, tag="_start_my_charm")

            self.framework.observe(
                self.stop_coordinator.on.coordinator_requested,
                self._on_coordinator_requested
            )
            self.framework.observe(
                self.stop_coordinator.on.coordinator_approved,
                self._on_coordinator_approved
            )
            self.framework.observe(
                self.start_coordinator.on.coordinator_requested,
                self._on_coordinator_requested
            )
            self.framework.observe(
                self.start_coordinator.on.coordinator_approved,
                self._on_coordinator_approved
            )

            def _a_method():
                # A method that kick starts the restarting coordination
                ......
                if self.charm.unit.is_leader():
                    self.stop_coordinator.coordinate()

            def _on_coordinator_requested(self, event):
                if self.service_is_running and event.tag == "_stop_my_charm":
                    # We are in the stop-phase
                    self.service.stop()
                    self.stop_coordinator.acknowledge(event)
                elif event.tag == "_start_my_charm":
                    # we are in the starting-phase
                    self.service.start()
                    self.start_coordinator.acknowledge(event)

            def _on_coordinator_approved(self, event):
                # All units have ack'ed the activity, which means we have stopped.
                if self.charm.unit.is_leader() and event.tag == "_stop_my_charm":
                    # Now kickstart the restarting process
                    self.start_coordinator.coordinate()
"""


import logging
from typing import AnyStr

from ops.charm import (
    CharmBase,
    CharmEvents,
    EventSource,
    RelationChangedEvent,
)
from ops.framework import EventBase, Handle, Object

logger = logging.getLogger(__name__)


class CoordinatorEventBase(EventBase):
    """Base event for the coordination activities."""

    def __init__(self, handle: "Handle", tag: str):
        super().__init__(handle)
        self._tag = tag

    @property
    def tag(self):
        """Returns the tag representing this coordinator's controllers."""
        return self._tag


class CoordinatorRequestedEvent(CoordinatorEventBase):
    """Event to signal that the leader requested the units to coordinate a new activity."""

    def __init__(self, handle: "Handle", tag: str):
        super().__init__(handle, tag)


class CoordinatorApprovedEvent(CoordinatorEventBase):
    """Event to signal that all units ack'ed the coordination request and can proceed."""

    def __init__(self, handle: "Handle", tag: str):
        super().__init__(handle, tag)


class CoordinatorCharmEvents(CharmEvents):
    """List of events that the TLS Certificates requirer charm can leverage."""

    coordinator_approved = EventSource(CoordinatorApprovedEvent)
    coordinator_requested = EventSource(CoordinatorRequestedEvent)


class CoordinatedOpsManager(Object):
    """Coordinates activities that demand the entire peer group to act at once."""

    on = CoordinatorCharmEvents()

    def __init__(self, charm: CharmBase, relation: AnyStr, tag: AnyStr = ""):
        super().__init__(charm, relation)
        self.tag = tag
        self.relation = relation
        self.app = charm.app
        self.name = relation + tag  # use the tag to separate multiple coordinator objects
        # in the same charm class.
        self.charm = charm  # Maintain a reference to charm, so we can emit events.
        self.framework.observe(charm.on[self.relation].relation_changed, self._on_relation_changed)

    @property
    def under_coordination(self):
        """Returns True if the _coord_approved == False."""
        return (
            self.model.get_relation(self.relation)
            .data[self.app]
            .get(f"_{self.name}_coord_approved", "True")
            == "False"
        )

    def coordinate(self):
        """Process a request to ask a new coordination activity.

        If we are the leader, fire off a coordinator requested event in the self.name.
        """
        logger.info("coordinate: starting")
        if self.charm.unit.is_leader():
            counter = int(
                self.model.get_relation(self.relation)
                .data[self.app]
                .get(f"_{self.name}_coord_counter", "0")
            )
            self.model.get_relation(self.relation).data[self.app][
                f"_{self.name}_coord_counter"
            ] = str(counter + 1 if counter < 10000000 else 0)
            self.model.get_relation(self.relation).data[self.app][
                f"_{self.name}_coord_approved"
            ] = "False"
            logger.info("coordinate: tasks executed")

    def acknowledge(self, event):
        """Runs the ack of the latest requested coordination.

        Each unit will set their own _counter to the same value as app's.
        """
        coord_counter = f"_{self.name}_coord_counter"
        self.model.get_relation(self.relation).data[self.charm.unit][coord_counter] = str(
            self.model.get_relation(self.relation).data[self.app].get(coord_counter, 0)
        )
        logger.info("acknowledge: updated internal counter")

        if not self.charm.unit.is_leader():
            # Nothing to do anymore.
            logger.info("acknowledge: this unit is not a leader")
            return

        relation = self.model.get_relation(self.relation)
        # Now, the leader must check if everyone has ack'ed
        for unit in relation.units:
            if relation.data[unit].get(coord_counter, "0") != relation.data[self.app].get(
                coord_counter, "0"
            ):
                logger.info(f"acknowledge: {unit.name} still has a different coord_counter")
                # We defer the event until _coord_approved == True.
                # If we have _coord_counter differing, then we are not yet there.
                event.defer()
                return
        logger.info("acknowledge: all units are set, set coord_approved == True")
        # Just confirmed we have all units ack'ed. Now, set the approval.
        relation.data[self.app][f"_{self.name}_coord_approved"] = "True"

    def _on_relation_changed(self: CharmBase, _: RelationChangedEvent):
        """Process relation changed.

        First, determine whether this unit has received a new request for coordination.

        Then, if we are the leader, fire off a coordinator requested event.
        """
        logger.info("coordinator: starting _on_relation_changed")
        relation_data = self.model.get_relation(self.relation).data[self.app]
        unit_data = self.model.get_relation(self.relation).data[self.charm.unit]

        if relation_data.get(f"_{self.name}_coord_approved", "False") == "True":
            logger.info("coordinator: _on_relation_changed -- coordinator approved")
            # We are approved to move on, issue the coordinator_approved event.
            self.on.coordinator_approved.emit(self.tag)
            return
        coord_counter = f"_{self.name}_coord_counter"
        if coord_counter in relation_data and relation_data.get(
            coord_counter, "0"
        ) != unit_data.get(coord_counter, "0"):
            logger.info("coordinator: _on_relation_changed -- coordinator requested")
            self.on.coordinator_requested.emit(self.tag)
            return
