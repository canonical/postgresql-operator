# Issue #691 Investigation Findings

## Bug Summary

**Issue**: `'NoneType' object has no attribute 'data'` error during `database-peers-relation-broken` hook when removing a PostgreSQL unit.

**Error location**: `src/charm.py`, line 479 in `_on_peer_relation_changed`:
```python
if "cluster_initialised" not in self._peers.data[self.app]:
```

**Fix**: PR #749 (commit `783ffbd08`, merged 2025-02-07). Both 14/stable (rev 987) and 14/candidate (rev 1029) contain the fix.

## Root Cause

The bug requires **three conditions** to occur simultaneously:

### 1. Juju Bug: `isPeerRelation` map loss after agent restart

In `worker/uniter/relation/statetracker.go`, the `isPeerRelation` map tracks which relations are peer relations. This map is populated in `SynchronizeScopes()` only for **newly discovered** relations. When the Juju machine agent restarts, `loadInitialState()` calls `joinRelation()` for existing relations, but `joinRelation()` does **not** set `isPeerRelation[id] = true`.

As a result, after an agent restart, `IsPeerRelation(id)` returns `(false, nil)` for existing peer relations. The `!isPeer` guard in `nextHookForRelation` (`worker/uniter/relation/resolver.go`) fails, incorrectly allowing `relation-broken` hooks to be dispatched for peer relations.

By design, Juju should **never** dispatch `relation-broken` for peer relations.

### 2. Deferred event survival via SQLite transaction rollback

The ops framework wraps all changes during a hook dispatch in a single SQLite transaction (opened with explicit `BEGIN` in `SQLiteStorage._setup()`, committed only by `framework.commit()` at the end of successful dispatch).

When a hook crashes:
1. `framework.commit()` is never called
2. `framework.close()` runs in the `finally` block
3. SQLite rolls back the uncommitted transaction
4. Any deferred event notices that were deleted during `reemit()` are **restored**

This means: if a unit is in a failed/error state where hooks keep crashing, deferred events are preserved indefinitely through repeated transaction rollbacks.

### 3. Unsafe peer data access in charm code (rev 411)

At revision 411, `_on_peer_relation_changed` (line 479) accesses `self._peers.data[self.app]` without checking if `self._peers` is `None`. The `_peers` property calls `self.model.get_relation(PEER)` which returns `None` when the peer relation doesn't exist (i.e., during `relation-broken`).

Five locations had this unsafe pattern (lines 440, 517, 972, 1248, 1442 at rev 411).

## Deferred Event Creation (Condition 2)

Deferred events happen **routinely** in this charm — no special infrastructure behavior is needed. Three events are all mapped to `_on_peer_relation_changed`:

```python
# src/charm.py lines 146-148
self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
self.framework.observe(self.on.secret_changed, self._on_peer_relation_changed)
self.framework.observe(self.on.secret_remove, self._on_peer_relation_changed)
```

The handler defers events at two points:
- **Line 481**: cluster not initialized yet (happens during bootstrap, scale-up)
- **Line 486**: leader unit and `_reconfigure_cluster` fails (Patroni API timeout → `RetryError` in `_add_members`)

Additionally, the charm has 20 `event.defer()` calls across the codebase — in `_on_config_changed`, `_on_peer_relation_departed`, `_on_start`, and others.

Secret rotation, peer relation changes during scale operations, and config changes all feed into `_on_peer_relation_changed` and can get deferred. The reporter's notice #172339 confirms thousands of events were processed over the unit's lifetime.

### Deferred event persistence

The critical requirement is not event *creation* (routine) but event *persistence* until `relation-broken`. For a deferred event to survive through all preceding hooks, one of these must hold:

1. **Leader unit with Patroni issues**: The handler hits line 486 (`_reconfigure_cluster` → `_add_members` → `RetryError`) and re-defers on every hook cycle. This happens when Patroni API calls time out.
2. **Hooks crashing consistently**: SQLite transaction rollback restores consumed deferred events. Any persistent error causing hooks to crash would preserve deferred events indefinitely.

### LXD version relevance

Reporter's environment: **LXD 5.21.2 LTS**. Reproduction performed on **LXD 5.21.4 LTS** (same track; 5.21.2 is no longer available in the snap store).

Changes between LXD 5.21.2 and 5.21.4 (via [5.21.3](https://discourse.ubuntu.com/t/lxd-5-21-3-lts-has-been-released/53768) and [5.21.4](https://discourse.ubuntu.com/t/lxd-5-21-4-lts-has-been-released/66602) release notes) include OVN networking improvements, VM live migration, API metrics, and cluster management changes — nothing affecting Juju agent hook dispatch, SQLite transactions, or container process stability.

The bug mechanism is entirely in Juju's `statetracker.go` (agent restart clearing the `isPeerRelation` map) and the charm's unsafe `self._peers.data[self.app]` access. LXD provides the container runtime but does not participate in hook dispatch, deferred event processing, or SQLite transaction handling. The LXD version is **not a factor** in this bug.

That said, LXD instability (any version) could *indirectly* contribute by causing:
- Container/machine restarts → Juju agent restarts → `isPeerRelation` map cleared (condition 1)
- Network issues between containers → Patroni API timeouts → events re-deferred (condition 2 persistence)

No LXD-version-specific behavior is required.

## Reproduction

Successfully reproduced on Juju 3.4.4 / LXD 5.21.4 LTS with PostgreSQL charm revision 411.

### Full traceback (reproduced)

```
unit-postgresql-16: 22:39:29 DEBUG Re-emitting deferred event <RelationChangedEvent via PostgresqlOperatorCharm/on/database_peers_relation_changed[12]>.
unit-postgresql-16: 22:39:29 ERROR Uncaught exception while in charm code:
Traceback (most recent call last):
  File "src/charm.py", line 1646, in <module>
    main(PostgresqlOperatorCharm)
  File "ops/main.py", line 544, in main
    manager.run()
  File "ops/main.py", line 520, in run
    self._emit()
  File "ops/main.py", line 506, in _emit
    self.framework.reemit()
  File "ops/framework.py", line 861, in reemit
    self._reemit()
  File "ops/framework.py", line 941, in _reemit
    custom_handler(event)
  File "src/charm.py", line 479, in _on_peer_relation_changed
    if "cluster_initialised" not in self._peers.data[self.app]:
AttributeError: 'NoneType' object has no attribute 'data'

hook "database-peers-relation-broken" (via hook dispatching script: dispatch) failed: exit status 1
```

### Reproduction steps (exact commands)

**Prerequisites**: Juju 3.4.4 controller, LXD 5.21.x LTS

#### 1. Deploy PostgreSQL rev 411 with 3 units

```bash
juju deploy postgresql --channel 14/stable --revision 411 -n 3
juju deploy data-integrator -n 2
juju deploy grafana-agent --channel 1/stable --base ubuntu@22.04
juju integrate postgresql data-integrator
juju integrate postgresql grafana-agent
# Wait for active/idle
juju status --watch 5s
```

Note: `grafana-agent` must use `--base ubuntu@22.04` to match postgresql's base.

#### 2. Identify the target unit

Pick a non-leader unit to remove (e.g., `postgresql/2` on machine 2). Get its `database-peers` relation ID and machine number:

```bash
juju show-unit postgresql/2 | grep -B1 'database-peers'
# Example output: relation-id: 1

juju show-unit postgresql/2 | grep 'machine:'
# Example output: machine: "2"
```

In the commands below, replace `UNIT=postgresql/2`, `UNIT_NUM=2`, `MACHINE_NUM=2`, and `REL_ID=1` with your actual values.

#### 3. Prevent hooks from consuming the deferred event

Add a syntax error at the top of `charm.py` so all hooks fail at import (before `reemit()` runs). This **must** be done before injecting the deferred event, otherwise the next hook dispatch will consume it.

```bash
juju ssh $UNIT "sudo sed -i '4a SYNTAX ERROR HERE @@@' \
  /var/lib/juju/agents/unit-postgresql-$UNIT_NUM/charm/src/charm.py"
```

#### 4. Inject a deferred `RelationChangedEvent` into `.unit-state.db`

Install sqlite3 on the unit's machine:
```bash
juju ssh $UNIT 'sudo apt-get install -y sqlite3'
```

Generate the pickle snapshot data (run locally with actual `REL_ID`):
```python
import pickle, binascii
data = {'relation_name': 'database-peers', 'relation_id': REL_ID}
print(binascii.hexlify(pickle.dumps(data)).decode())
```

Inject the notice and snapshot (replace `REL_ID` and hex blob):
```bash
DB=/var/lib/juju/agents/unit-postgresql-$UNIT_NUM/charm/.unit-state.db

juju ssh $UNIT "sudo sqlite3 $DB \
  \"INSERT INTO notice (sequence, event_path, observer_path, method_name) \
    VALUES (1, 'PostgresqlOperatorCharm/on/database_peers_relation_changed[$REL_ID]', \
    'PostgresqlOperatorCharm', '_on_peer_relation_changed');\""

juju ssh $UNIT "sudo sqlite3 $DB \
  \"INSERT INTO snapshot (handle, data) \
    VALUES ('PostgresqlOperatorCharm/on/database_peers_relation_changed[$REL_ID]', \
    X'<hex blob from step above>');\""
```

Verify both entries exist:
```bash
juju ssh $UNIT "sudo sqlite3 $DB 'SELECT * FROM notice; SELECT handle FROM snapshot;'"
```

#### 5. Restart the Juju machine agent (triggers Bug A)

This clears the `isPeerRelation` map, causing peer `relation-broken` to be dispatched:
```bash
juju ssh $UNIT "sudo systemctl restart jujud-machine-$MACHINE_NUM.service"
```

#### 6. Remove the unit

```bash
juju remove-unit $UNIT --no-prompt
```

All hooks will fail at import (syntax error). The deferred event stays untouched in SQLite.

#### 7. Skip hooks until `database-peers-relation-broken`

Repeatedly resolve with `--no-retry` to skip each failing hook:
```bash
# Repeat until status shows: hook failed: "database-peers-relation-broken"
juju resolved --no-retry $UNIT
sleep 8
juju status | grep $UNIT
# Repeat as needed (typically 10-12 times)
```

#### 8. Fix the charm and let `database-peers-relation-broken` run

Remove the syntax error:
```bash
juju ssh $UNIT "sudo sed -i '/SYNTAX ERROR HERE/d' \
  /var/lib/juju/agents/unit-postgresql-$UNIT_NUM/charm/src/charm.py"
```

Resolve with retry:
```bash
juju resolved $UNIT
```

#### 9. Observe the crash

```bash
juju debug-log --include unit-postgresql-$UNIT_NUM --replay --no-tail | grep -A15 'Uncaught exception'
```

Expected output:
```
Re-emitting deferred event <RelationChangedEvent via PostgresqlOperatorCharm/on/database_peers_relation_changed[REL_ID]>.
Uncaught exception while in charm code:
Traceback (most recent call last):
  ...
  File "src/charm.py", line 479, in _on_peer_relation_changed
    if "cluster_initialised" not in self._peers.data[self.app]:
AttributeError: 'NoneType' object has no attribute 'data'

hook "database-peers-relation-broken" failed: exit status 1
```

### Natural trigger scenario

In the reporter's case, the likely sequence was:

1. A unit accumulated deferred events during normal operations (event notice #172339). Events mapped to `_on_peer_relation_changed` include `relation_changed`, `secret_changed`, and `secret_remove` — all routine in a running cluster.
2. The unit entered a "FAILED" state (Patroni down, hooks crashing). Crashing hooks preserved deferred events via SQLite transaction rollback.
3. The Juju machine agent restarted at some point (machine reboot, agent crash, OOM kill, or LXD container restart). This cleared the `isPeerRelation` map (Juju Bug A).
4. The user attempted to remove the failed unit (`juju remove-unit`).
5. Preceding hooks (departed, etc.) continued to crash due to the persistent failure, preserving the deferred event through transaction rollbacks.
6. `database-peers-relation-broken` fired (incorrectly, due to Juju Bug A).
7. The deferred event re-emitted during `reemit()`, calling `_on_peer_relation_changed` which crashed at `self._peers.data[self.app]` because `self._peers` is `None` during `relation-broken`.

## Key Technical Details

### `.unit-state.db` schema

```sql
CREATE TABLE notice (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_path TEXT,
    observer_path TEXT,
    method_name TEXT
);

CREATE TABLE snapshot (
    handle TEXT PRIMARY KEY,
    data BLOB  -- Python pickle
);
```

### Ops framework dispatch flow

```
run()
  try:
    _emit()
      reemit()          -- re-emit deferred events (notice deletions within transaction)
      _emit_charm_event() -- main hook event
      _evaluate_status()  -- collect-status
    _commit()
      framework.commit() -- commits SQLite transaction
  finally:
    framework.close()    -- closes connection (rolls back if uncommitted)
```

### Juju statetracker bug location

- `worker/uniter/relation/statetracker.go`: `SynchronizeScopes()` sets `isPeerRelation[id] = true` only for new relations; `loadInitialState()` → `joinRelation()` does not
- `worker/uniter/relation/resolver.go`: `nextHookForRelation()` uses `IsPeerRelation()` to guard against peer `relation-broken`
- Related Juju issues: GitHub #20713 (peer relation-broken during K8s refresh), Launchpad Bug #2076599 (relation-ids excludes peer relations)

## Fix Details (PR #749)

The fix replaced all unsafe `self._peers.data[self.app]` read accesses with safe properties:

```python
@property
def app_peer_data(self) -> DataMapping:
    """Return the app peer relation data."""
    relation = self.model.get_relation(PEER)
    if relation is None:
        return {}
    return relation.data[self.app]

@property
def is_cluster_initialised(self) -> bool:
    return "cluster_initialised" in self.app_peer_data
```

This ensures the charm handles `None` peer relations gracefully, which can occur during the (incorrectly dispatched) `database-peers-relation-broken` hook.
