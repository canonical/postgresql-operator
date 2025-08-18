# Refresh (upgrade)

```{admonition} Emergency stop button
:class: attention
Use `juju config appname pause-after-unit-refresh=all` to halt an in-progress refresh. Then, consider [rolling back](/how-to/refresh/rollback)
```

Charmed PostgreSQL supports minor {term}`in-place` {term}`refresh` via the [`juju refresh`](https://documentation.ubuntu.com/juju/3.6/reference/juju-cli/list-of-juju-cli-commands/refresh/#details) command.

## Recommended refreshes

These refreshes are well-tested and should be preferred.

```{eval-rst}
+-------------+------------+------------+------------+
| .. centered:: From       | .. centered:: To        |
+-------------+------------+------------+------------+
| Revision    | PostgreSQL | Revision   | PostgreSQL |
|             | Version    |            | Version    |
+=============+============+============+============+
| `843, 844`_ | 16.9       | TODO       | 16.9       |
+-------------+------------+------------+------------+
```

## Supported refreshes

These refreshes should be supported. If possible, use a [recommended refresh](#recommended-refreshes) instead.

```{eval-rst}
+-------------+------------+------------+------------+
| .. centered:: From       | .. centered:: To        |
+-------------+------------+------------+------------+
| Revision    | PostgreSQL | Revision   | PostgreSQL |
|             | Version    |            | Version    |
+=============+============+============+============+
| `843, 844`_ | 16.9       | TODO       | 16.9       |
|             |            +------------+------------+
|             |            | TODO       | 16.10      |
+-------------+------------+------------+------------+
```

## Non-supported refreshes
These refreshes are not supported {term}`in-place`. In some of these cases, it may be possible to perform an out-of-place upgrade or downgrade.

* Minor in-place {term}`downgrade` from PostgreSQL 16.10 to 16.9
* Major in-place {term}`upgrade` from PostgreSQL 14 to 16
* Major in-place {term}`downgrade` from PostgreSQL 16 to 14
* Any refresh from or to a non-stable version (e.g. 16/edge)

<!--TODO: When ready, point to 14-16 migration guide -->

```{eval-rst}
.. _843, 844: https://github.com/canonical/postgresql-operator/releases/tag/v16%2F1.59.0
```

```{toctree}
:titlesonly:
:hidden:

Perform a minor upgrade <minor-upgrade>
Roll back an in-progress refresh <rollback>
```
