# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release of Charmed PostgreSQL 16.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases page on GitHub](https://github.com/canonical/postgresql-operator/releases).

## Dependencies and supported features

**PostgreSQL 16** comes with all features supported by PostgreSQL 14: TLS encryption, COS monitoring, upgrades, cross-regional async replication, LDAP, Point-in-time recovery, and {doc}`more <postgresql-14:reference/releases>`.

In addition, Charmed PostgreSQL 16 supports new features like [Juju spaces](/how-to/deploy/juju-spaces), [Juju storage](/how-to/deploy/juju-storage), and [Juju user secrets](https://documentation.ubuntu.com/juju/latest/reference/secret/index.html#user). 

For more details about all new PostgreSQL 16 features, see the complete [release notes](https://github.com/canonical/postgresql-operator/releases/tag/v16%2F1.59.0)

| Charmhub revision</br>(amd, arm) | Snap revision</br>(amd, arm) | PostgreSQL version | Minimum Juju version |
|:----------------------------:|:------------------------:|:------------------:|:--------------------:|
|           [843, 844]         |         218, 219         |        16.9        |         3.6        | 

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

See: [`juju info`](https://juju.is/docs/juju/juju-info).
```

<!--Links-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

[843, 844]: https://github.com/canonical/postgresql-operator/releases/tag/v16%2F1.59.0
