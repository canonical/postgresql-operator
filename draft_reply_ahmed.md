Hi Ahmed,

Thanks for the details. Since both rev 987 and rev 1045 already include pgAudit in `shared_preload_libraries`, the upgrade itself shouldn't cause this.

Looking at the code more carefully, there are actually **two separate issues** in the customer's report:

1. **The blocked status** ("Cannot disable plugins: Existing objects depend on it") — this is caused by a `DependentObjectsStillExist` error. The charm tries to `DROP EXTENSION` for every plugin whose config option is `False` (the default). If Landscape created an extension that's in the charm's plugin list (e.g., `pg_trgm`, `btree_gin`, `hstore`, etc.) and there are database objects depending on it, the drop fails and the charm blocks.

2. **The pgaudit traceback** — a secondary error that's only logged but does **not** cause the blocked status.

To unblock the charm, we need to identify which extension has dependent objects and tell the charm to keep it enabled. Could you ask the customer to run:

```
juju debug-log --replay | grep "Failed to disable plugin"
```

That will show which extension is the problem. Then the fix is:

```
juju config postgresql plugin_<extension_name>_enable=True
```

The charm retries on `update-status`, so it should unblock automatically after that.

For the additional diagnostics on the pgaudit side, these would also be helpful:

```
juju ssh postgresql/0 -- "sudo grep shared_preload /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml"
juju ssh postgresql/0 -- "sudo grep shared_preload /var/snap/charmed-postgresql/common/var/lib/postgresql/postgresql.conf"
juju ssh postgresql/0 -- "snap info charmed-postgresql | grep -E 'installed|tracking'"
juju ssh postgresql/0 -- "sudo cat /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql*.log | grep -i pgaudit | tail -20"
```

This tells us whether pgAudit is properly configured and whether the snap is at the expected revision (247 for amd64, 246 for arm64).
