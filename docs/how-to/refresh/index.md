# Refresh (upgrade)

Charmed PostgreSQL supports minor {term}`in-place` {term}`refresh` via the [`juju refresh`](https://documentation.ubuntu.com/juju/3.6/reference/juju-cli/list-of-juju-cli-commands/refresh/#details) command.

```{admonition} Emergency stop button
:class: attention
Use `juju config appname pause-after-unit-refresh=all` to halt an in-progress refresh
```

## Supported refreshes

**Minor in-place {term}`upgrade`** between stable releases. 
E.g. PostgreSQL 16.9 is upgraded to 16.10

> See [How to perform a minor upgrade](/how-to/refresh/minor-upgrade)

**Minor in-place {term}`rollback`** between stable releases. 
E.g. An upgrade from PostgreSQL 16.9 -> 16.10 fails, so a rollback is triggered to take all units from 16.10 back to 16.9.

> See [How to perform a minor rollback](/how-to/refresh/minor-rollback)

<!-- TODO: Add when new stable: * Minor in-place upgrade from Revision X to Y -->

Check all available Charmed PostgreSQL 16 versions in [](/reference/releases).

## Non-supported refreshes
* Minor in-place {term}`downgrade` from PostgreSQL 16.10 to 16.9
* Major in-place {term}`upgrade` from PostgreSQL 14 to 16
* Major in-place {term}`downgrade` from PostgreSQL 16 to 14
* Any refresh involving non-stable versions (e.g. 16/edge)

The actions listed above must be performed as {term}`out of place` upgrades.

<!--TODO: When ready, point to 14-16 migration guide -->


```{toctree}
:titlesonly:
:hidden:

Perform a minor upgrade <minor-upgrade>
Perform a minor rollback <minor-rollback>
```