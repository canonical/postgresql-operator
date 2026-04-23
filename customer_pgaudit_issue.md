# Customer Issue: pgAudit `UndefinedObject` / "Cannot disable plugins"

## Symptom

Unit blocked with:
```
Cannot disable plugins: Existing objects depend on it. See logs
```

Traceback in debug-log:
```
psycopg2.errors.UndefinedObject: unrecognized configuration parameter "pgaudit.log"
```

## Customer environment

- Charm: **charmed postgresql VM** (not K8s)
- Current revision: **1045** (14/stable, PostgreSQL 14.20, snap 247)
- Previous revision: **987** (14/stable, PostgreSQL 14.20, snap 245)
- Trigger: customer modified `experimental_max_connections` config option
- Database is still functional (blocked status appears cosmetic)
- Field engineer (Ahmed) could **not reproduce** by upgrading 987 → 1045 with `pre-upgrade-check`

## Analysis

### Upgrade did NOT cross the pgAudit boundary

Both rev 987 and rev 1045 already have `shared_preload_libraries: 'timescaledb,pgaudit'`. The pgaudit boundary was at rev 475 (stable: rev 553). So the original theory of upgrading from a pre-pgaudit revision is **ruled out** for this customer.

| Stable revision | PostgreSQL | Snap | `shared_preload_libraries` |
|---|---|---|---|
| 468/467 | 14.12 | — | `timescaledb` (NO pgaudit) |
| 553/552 | 14.15 | — | `timescaledb,pgaudit` (pgaudit added) |
| 986/987 | 14.20 | 245/243 | `timescaledb,pgaudit` |
| **1045/1044** | **14.20** | **247/246** | **`timescaledb,pgaudit`** |

### What happens when `experimental_max_connections` is changed

1. `config-changed` fires → `_on_config_changed()` (`charm.py:1183`)
2. `update_config()` is called → renders new `patroni.yml.j2`, pushes `max_connections` to Patroni API via `bulk_update_parameters_controller_by_patroni`
3. `max_connections` is a restart-required parameter → Patroni marks `pending_restart`
4. `_handle_postgresql_restart_need()` detects `pending_restart`, emits `acquire_lock` (restart happens **asynchronously** in a later event via rolling ops manager)
5. Control returns to `_on_config_changed()` → **`enable_disable_extensions()`** runs (`charm.py:1217`)
6. `enable_disable_extensions()` unconditionally calls `_configure_pgaudit(False)` (`postgresql.py:452`)
7. This executes `ALTER SYSTEM RESET pgaudit.log;` — which should work if pgaudit is loaded

### Why it might still fail

PostgreSQL should still be running with pgaudit loaded at step 6 (the restart hasn't happened yet). Possible explanations:

1. **pgaudit failed to load at some earlier PostgreSQL restart** — if for any reason pgaudit was removed from shared_preload_libraries in the DCS (Distributed Configuration Store) or the .so file was unavailable during a previous restart, PostgreSQL could be running without pgaudit even though the Patroni YAML says it should be there.

2. **Patroni's SIGHUP-triggered reload caused an unexpected restart** — `_handle_postgresql_restart_need` calls `reload_patroni_configuration()` which sends SIGHUP to Patroni. If Patroni detects that shared_preload_libraries differs between YAML and DCS, it could trigger a PostgreSQL restart. If `_configure_pgaudit(False)` runs during this brief restart window, the connection could land on a PostgreSQL process that hasn't finished loading pgaudit.

3. **The error and the blocked status may come from different events** — the `UndefinedObject` traceback might be from one event, while the "Cannot disable plugins" blocked status could be from a `DependentObjectsStillExist` error on a different extension in a separate event. The customer might have extensions with dependent objects (like Landscape creating extensions that the charm tries to drop).

## Key diagnostics to ask the customer

### 1. Check if pgaudit is configured in Patroni and PostgreSQL (validated)

```
juju ssh postgresql/0 -- "sudo grep shared_preload /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml"
juju ssh postgresql/0 -- "sudo grep shared_preload /var/snap/charmed-postgresql/common/var/lib/postgresql/postgresql.conf"
```

Both should show `timescaledb,pgaudit`. If pgaudit is missing from `postgresql.conf` but present in `patroni.yaml`, Patroni hasn't synced the config properly.

### 2. Check the snap revision (validated)

```
juju ssh postgresql/0 -- "snap info charmed-postgresql | grep -E 'installed|tracking'"
```

Expected for rev 1045 (amd64): snap revision **247**. If the snap is on an older revision, the snap refresh may have failed during upgrade. If it's on a very old snap (pre-124), the pgaudit `.so` library doesn't exist on disk — PostgreSQL would fail to load pgaudit even though Patroni config says to.

| Charm revision | Expected snap (amd64) | pgaudit in snap? |
|---|---|---|
| 468 | 120 | No |
| 475+ | 124+ | Yes |
| 987 | 245 | Yes |
| 1045 | 247 | Yes |

### 3. Check PostgreSQL logs for pgaudit load failure (validated)

```
juju ssh postgresql/0 -- "sudo cat /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql*.log | grep -i pgaudit | tail -20"
```

If pgaudit failed to load, there should be a `FATAL` or `WARNING` line like `could not access file "pgaudit": No such file or directory`.

### 4. Other questions

- Was the charm upgraded from any revision **before 553** at any point in the deployment's history (even if not the most recent upgrade)?
- What does `juju debug-log --replay | grep "Failed to disable plugin"` show? (Reveals if there's a `DependentObjectsStillExist` error for a specific extension.)
- Full debug-log around the time `experimental_max_connections` was changed.

### Note on psql access

`charmed-postgresql.psql` via `sudo -u snap_daemon` does NOT work on Noble (24.04) due to the snap_daemon → _daemon_ user rename and snap home directory restrictions. Direct file inspection (patroni.yaml, postgresql.conf, PG logs) is more reliable for diagnostics.

## Relevant code

- `lib/charms/postgresql_k8s/v0/postgresql.py:151-168` — `_configure_pgaudit()` method, no guard for `UndefinedObject`
- `lib/charms/postgresql_k8s/v0/postgresql.py:452` — unconditional call to `_configure_pgaudit(False)`
- `src/charm.py:1183-1217` — `_on_config_changed` → `update_config()` → `enable_disable_extensions()`
- `src/charm.py:2151-2186` — `_api_update_config()` pushes `max_connections` to Patroni API
- `src/charm.py:2322-2359` — `_handle_postgresql_restart_need()` — reload + deferred restart
- `templates/patroni.yml.j2:106,139` — `shared_preload_libraries` with pgaudit

## Upstream issue

- [canonical/postgresql-k8s-operator#701](https://github.com/canonical/postgresql-k8s-operator/issues/701) (open, Jira: DPE-5494)
- Lib version: LIBPATCH 56 (same in both VM and K8s charms, bug not yet fixed)

## Lib bug (regardless of root cause)

`_configure_pgaudit()` should catch `psycopg2.errors.UndefinedObject` to handle the case where pgaudit is not loaded. This would prevent the error from cascading:

```python
def _configure_pgaudit(self, enable: bool) -> None:
    connection = None
    try:
        connection = self._connect_to_database()
        connection.autocommit = True
        with connection.cursor() as cursor:
            if enable:
                cursor.execute("ALTER SYSTEM SET pgaudit.log = 'ROLE,DDL,MISC,MISC_SET';")
                cursor.execute("ALTER SYSTEM SET pgaudit.log_client TO off;")
                cursor.execute("ALTER SYSTEM SET pgaudit.log_parameter TO off;")
            else:
                cursor.execute("ALTER SYSTEM RESET pgaudit.log;")
                cursor.execute("ALTER SYSTEM RESET pgaudit.log_client;")
                cursor.execute("ALTER SYSTEM RESET pgaudit.log_parameter;")
            cursor.execute("SELECT pg_reload_conf();")
    except psycopg2.errors.UndefinedObject:
        logger.warning("pgaudit not loaded in shared_preload_libraries, skipping configuration")
    finally:
        if connection is not None:
            connection.close()
```

## Important: two separate issues

The blocked status and the pgaudit traceback are likely **two different problems**:

1. **Blocked status** ("Cannot disable plugins: Existing objects depend on it") — caused by `DependentObjectsStillExist` (`charm.py:1268`). Some extension has dependent objects (indexes, views, etc.) that prevent the charm from dropping it. The pgaudit `UndefinedObject` error does NOT cause this blocked status — it's caught as `PostgreSQLEnableDisableExtensionError` and only logged.

2. **pgaudit UndefinedObject traceback** — a secondary error from `_configure_pgaudit(False)` failing because pgaudit isn't loaded. This is logged but does not set the blocked status.

## Workaround

### For the blocked status (primary issue)

Identify which extension has dependent objects:
```
juju debug-log --replay | grep "Failed to disable plugin"
```

Then enable it via config so the charm stops trying to drop it:
```
juju config postgresql plugin_<extension_name>_enable=True
```

The charm retries `enable_disable_extensions()` on every `update-status` when blocked with this message (`charm.py:1771`), so it should unblock automatically once the extension is enabled in config.

### For the pgaudit error (secondary issue)

Restarting Patroni may help if pgaudit isn't loaded in the running PostgreSQL:
```
juju ssh postgresql/0 -- sudo snap restart charmed-postgresql.patroni
```

But this alone will **not** clear the blocked status.
