# Releases

Charmed PostgreSQL 16 supports all [features listed in PostgreSQL 14](https://canonical-charmed-postgresql.readthedocs-hosted.com/14/reference/releases/#dependencies-and-supported-features).

| Release | PostgreSQL version | Minimum Juju version | 
|:---:|:---:|:---:|
| [843, 844] | 16.9  | 3.6+  |

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases page on GitHub](https://github.com/canonical/postgresql-operator/releases).

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

Check the tables below, or use [`juju info`](https://juju.is/docs/juju/juju-info).
```

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/reference/plugins-extensions).

<!-- LINKS -->
[843, 844]: https://github.com/canonical/postgresql-operator/releases/tag/v16%2F1.59.0

<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

