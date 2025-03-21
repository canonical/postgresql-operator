# Copyright 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This library enables "rolling" operations across units of a charmed Application.

For example, a charm author might use this library to implement a "rolling restart", in
which all units in an application restart their workload, but no two units execute the
restart at the same time.

To implement the rolling restart, a charm author would do the following:

1. Add a peer relation called 'restart' to a charm's `metadata.yaml`:
```yaml
peers:
    restart:
        interface: rolling_op
```

Import this library into src/charm.py, and initialize a RollingOpsManager in the Charm's
`__init__`. The Charm should also define a callback routine, which will be executed when
a unit holds the distributed lock:

src/charm.py
```python
# ...
from charms.rolling_ops.v0.rollingops import RollingOpsManager
# ...
class SomeCharm(...):
    def __init__(...)
        # ...
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )
        # ...
    def _restart(self, event):
        systemd.service_restart('foo')
```

To kick off the rolling restart, emit this library's AcquireLock event. The simplest way
to do so would be with an action, though it might make sense to acquire the lock in
response to another event.

```python
    def _on_trigger_restart(self, event):
        self.charm.on[self.restart_manager.name].acquire_lock.emit()
```

In order to trigger the restart, a human operator would execute the following command on
the CLI:

```
juju run-action some-charm/0 some-charm/1 <... some-charm/n> restart
```

Note that all units that plan to restart must receive the action and emit the acquire
event. Any units that do not run their acquire handler will be left out of the rolling
restart. (An operator might take advantage of this fact to recover from a failed rolling
operation without restarting workloads that were able to successfully restart -- simply
omit the successful units from a subsequent run-action call.)

"""

import logging
from enum import Enum
from typing import AnyStr, Callable, Optional

from ops.charm import ActionEvent, CharmBase, RelationChangedEvent
from ops.framework import EventBase, Object
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "20b7777f58fe421e9a223aefc2b4d3a4"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 8


class LockNoRelationError(Exception):
    """Raised if we are trying to process a lock, but do not appear to have a relation yet."""

    pass


class LockState(Enum):
    """Possible states for our Distributed lock.

    Note that there are two states set on the unit, and two on the application.

    """

    ACQUIRE = "acquire"
    RELEASE = "release"
    GRANTED = "granted"
    IDLE = "idle"


class Lock:
    """A class that keeps track of a single asynchronous lock.

    Warning: a Lock has permission to update relation data, which means that there are
    side effects to invoking the .acquire, .release and .grant methods. Running any one of
    them will trigger a RelationChanged event, once per transition from one internal
    status to another.

    This class tracks state across the cloud by implementing a peer relation
    interface. There are two parts to the interface:

    1) The data on a unit's peer relation (defined in metadata.yaml.) Each unit can update
       this data. The only meaningful values are "acquire", and "release", which represent
       a request to acquire the lock, and a request to release the lock, respectively.

    2) The application data in the relation. This tracks whether the lock has been
       "granted", Or has been released (and reverted to idle). There are two valid states:
       "granted" or None.  If a lock is in the "granted" state, a unit should emit a
       RunWithLocks event and then release the lock.

       If a lock is in "None", this means that a unit has not yet requested the lock, or
       that the request has been completed.

    In more detail, here is the relation structure:

    relation.data:
        <unit n>:
            status: 'acquire|release'
        <application>:
           <unit n>: 'granted|None'

    Note that this class makes no attempts to timestamp the locks and thus handle multiple
    requests in a row. If a unit re-requests a lock before being granted the lock, the
    lock will simply stay in the "acquire" state. If a unit wishes to clear its lock, it
    simply needs to call lock.release().

    """

    def __init__(self, manager, unit=None):
        self.relation = manager.model.relations[manager.name][0]
        if not self.relation:
            # TODO: defer caller in this case (probably just fired too soon).
            raise LockNoRelationError()

        self.unit = unit or manager.model.unit
        self.app = manager.model.app

    @property
    def _state(self) -> LockState:
        """Return an appropriate state.

        Note that the state exists in the unit's relation data, and the application
        relation data, so we have to be careful about what our states mean.

        Unit state can only be in "acquire", "release", "None" (None means unset)
        Application state can only be in "granted" or "None" (None means unset or released)

        """
        unit_state = LockState(self.relation.data[self.unit].get("state", LockState.IDLE.value))
        app_state = LockState(
            self.relation.data[self.app].get(str(self.unit), LockState.IDLE.value)
        )

        if app_state == LockState.GRANTED and unit_state == LockState.RELEASE:
            # Active release request.
            return LockState.RELEASE

        if app_state == LockState.IDLE and unit_state == LockState.ACQUIRE:
            # Active acquire request.
            return LockState.ACQUIRE

        logger.debug("Lock state: %s %s", unit_state, app_state)
        return app_state  # Granted or unset/released

    @_state.setter
    def _state(self, state: LockState):
        """Set the given state.

        Since we update the relation data, this may fire off a RelationChanged event.
        """
        if state == LockState.ACQUIRE:
            self.relation.data[self.unit].update({"state": state.value})

        if state == LockState.RELEASE:
            self.relation.data[self.unit].update({"state": state.value})

        if state == LockState.GRANTED:
            self.relation.data[self.app].update({str(self.unit): state.value})

        if state is LockState.IDLE:
            self.relation.data[self.app].update({str(self.unit): state.value})

        logger.debug("state: %s", state.value)

    def acquire(self):
        """Request that a lock be acquired."""
        self._state = LockState.ACQUIRE
        logger.debug("Lock acquired.")

    def release(self):
        """Request that a lock be released."""
        self._state = LockState.RELEASE
        logger.debug("Lock released.")

    def clear(self):
        """Unset a lock."""
        self._state = LockState.IDLE
        logger.debug("Lock cleared.")

    def grant(self):
        """Grant a lock to a unit."""
        self._state = LockState.GRANTED
        logger.debug("Lock granted.")

    def is_held(self):
        """This unit holds the lock."""
        return self._state == LockState.GRANTED

    def release_requested(self):
        """A unit has reported that they are finished with the lock."""
        return self._state == LockState.RELEASE

    def is_pending(self):
        """Is this unit waiting for a lock?"""
        return self._state == LockState.ACQUIRE


class Locks:
    """Generator that returns a list of locks."""

    def __init__(self, manager):
        self.manager = manager

        # Gather all the units.
        relation = manager.model.relations[manager.name][0]
        units = list(relation.units)

        # Plus our unit ...
        units.append(manager.model.unit)

        self.units = units

    def __iter__(self):
        """Yields a lock for each unit we can find on the relation."""
        for unit in self.units:
            yield Lock(self.manager, unit=unit)


class RunWithLock(EventBase):
    """Event to signal that this unit should run the callback."""

    pass


class AcquireLock(EventBase):
    """Signals that this unit wants to acquire a lock."""

    def __init__(self, handle, callback_override: Optional[str] = None):
        super().__init__(handle)
        self.callback_override = callback_override or ""

    def snapshot(self):
        """Snapshot of lock event."""
        return {"callback_override": self.callback_override}

    def restore(self, snapshot):
        """Restores lock event."""
        self.callback_override = snapshot["callback_override"]


class ProcessLocks(EventBase):
    """Used to tell the leader to process all locks."""

    pass


class RollingOpsManager(Object):
    """Emitters and handlers for rolling ops."""

    def __init__(self, charm: CharmBase, relation: AnyStr, callback: Callable):
        """Register our custom events.

        params:
            charm: the charm we are attaching this to.
            relation: an identifier, by convention based on the name of the relation in the
                metadata.yaml, which identifies this instance of RollingOperatorsFactory,
                distinct from other instances that may be handling other events.
            callback: a closure to run when we have a lock. (It must take a CharmBase object and
                EventBase object as args.)
        """
        # "Inherit" from the charm's class. This gives us access to the framework as
        # self.framework, as well as the self.model shortcut.
        super().__init__(charm, None)

        self.name = relation
        self._callback = callback
        self.charm = charm  # Maintain a reference to charm, so we can emit events.

        charm.on.define_event("{}_run_with_lock".format(self.name), RunWithLock)
        charm.on.define_event("{}_acquire_lock".format(self.name), AcquireLock)
        charm.on.define_event("{}_process_locks".format(self.name), ProcessLocks)

        # Watch those events (plus the built in relation event).
        self.framework.observe(charm.on[self.name].relation_changed, self._on_relation_changed)
        self.framework.observe(charm.on[self.name].acquire_lock, self._on_acquire_lock)
        self.framework.observe(charm.on[self.name].run_with_lock, self._on_run_with_lock)
        self.framework.observe(charm.on[self.name].process_locks, self._on_process_locks)
        self.framework.observe(charm.on.leader_elected, self._on_process_locks)

    def _callback(self: CharmBase, event: EventBase) -> None:
        """Placeholder for the function that actually runs our event.

        Usually overridden in the init.
        """
        raise NotImplementedError

    def _on_relation_changed(self: CharmBase, event: RelationChangedEvent):
        """Process relation changed.

        First, determine whether this unit has been granted a lock. If so, emit a RunWithLock
        event.

        Then, if we are the leader, fire off a process locks event.

        """
        lock = Lock(self)

        if lock.is_pending():
            self.model.unit.status = WaitingStatus("Awaiting {} operation".format(self.name))

        if lock.is_held():
            self.charm.on[self.name].run_with_lock.emit()

        if self.model.unit.is_leader():
            self.charm.on[self.name].process_locks.emit()

    def _on_process_locks(self: CharmBase, event: ProcessLocks):
        """Process locks.

        Runs only on the leader. Updates the status of all locks.

        """
        if not self.model.unit.is_leader():
            return

        pending = []

        for lock in Locks(self):
            if lock.is_held():
                # One of our units has the lock -- return without further processing.
                return

            if lock.release_requested():
                lock.clear()  # Updates relation data

            if lock.is_pending():
                if lock.unit == self.model.unit:
                    # Always run on the leader last.
                    pending.insert(0, lock)
                else:
                    pending.append(lock)

        # If we reach this point, and we have pending units, we want to grant a lock to
        # one of them.
        if pending:
            self.model.app.status = MaintenanceStatus("Beginning rolling {}".format(self.name))
            lock = pending[-1]
            lock.grant()
            if lock.unit == self.model.unit:
                # It's time for the leader to run with lock.
                self.charm.on[self.name].run_with_lock.emit()
            return

        if self.model.app.status.message == f"Beginning rolling {self.name}":
            self.model.app.status = ActiveStatus()

    def _on_acquire_lock(self: CharmBase, event: ActionEvent):
        """Request a lock."""
        try:
            Lock(self).acquire()  # Updates relation data
            # emit relation changed event in the edge case where acquire does not
            relation = self.model.get_relation(self.name)

            # persist callback override for eventual run
            relation.data[self.charm.unit].update({"callback_override": event.callback_override})
            self.charm.on[self.name].relation_changed.emit(relation, app=self.charm.app)

        except LockNoRelationError:
            logger.debug("No {} peer relation yet. Delaying rolling op.".format(self.name))
            event.defer()

    def _on_run_with_lock(self: CharmBase, event: RunWithLock):
        lock = Lock(self)
        self.model.unit.status = MaintenanceStatus("Executing {} operation".format(self.name))
        relation = self.model.get_relation(self.name)

        # default to instance callback if not set
        callback_name = relation.data[self.charm.unit].get(
            "callback_override", self._callback.__name__
        )
        callback = getattr(self.charm, callback_name)
        callback(event)

        lock.release()  # Updates relation data
        if lock.unit == self.model.unit:
            self.charm.on[self.name].process_locks.emit()

        # cleanup old callback overrides
        relation.data[self.charm.unit].update({"callback_override": ""})

        if self.model.unit.status.message == f"Executing {self.name} operation":
            self.model.unit.status = ActiveStatus()
