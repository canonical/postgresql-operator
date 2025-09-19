---
relatedlinks: "[Charm&#32risk](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk)"
---

# Releases

Charmed PostgreSQL 16 supports all [features listed in PostgreSQL 14](https://canonical-charmed-postgresql.readthedocs-hosted.com/14/reference/releases/#dependencies-and-supported-features).

| Release | PostgreSQL version | Minimum Juju version | 
|:---:|:---:|:---:|
| [843, 844] | 16.9  | 3.6+  |

See all release notes on [GitHub](https://github.com/canonical/postgresql-operator/releases).

## How to refresh (upgrade)

Charmed PostgreSQL supports **minor in-place upgrades**. See [](/how-to/refresh) for more information.

[Contact us](/reference/contacts) if you are interested in migrating from PostgreSQL 14 to 16.

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

<!--TODO: Move to explanation -->


<!-- LINKS -->
[843, 844]: https://github.com/canonical/postgresql-operator/releases/tag/v16%2F1.59.0

<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

