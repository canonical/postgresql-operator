# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release of Charmed PostgreSQL 14.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases page on GitHub](https://github.com/canonical/postgresql-operator/releases).

## Dependencies and supported features

The table below shows information for all minor releases of Charmed PostgreSQL 14.

| Release | PostgreSQL version | Juju 3 version | [TLS encryption](/how-to/enable-tls)* | [COS monitoring](/how-to/monitoring-cos/enable-monitoring) | [Minor version upgrades](/how-to/upgrade/perform-a-minor-upgrade) | [Cross-regional async replication](/how-to/cross-regional-async-replication/index) | [Point-in-time recovery](/how-to/back-up-and-restore/restore-a-backup) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| [552], [553] | 14.15 | `3.6.1+` | ![check] | ![check] | ![check] | ![check] | ![check] |
| [467], [468] | 14.12 | `3.4.3+` | ![check] | ![check] | ![check] | ![check] | ![check] |
| [429], [430] | 14.11 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] |  |
| [363] | 14.10 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] |  |
| [351] | 14.9 | `3.1.6+` |  | ![check] | ![check] |  |  |
| [336] | 14.9 | `3.1.5+` |  | ![check] | ![check] |  |  |
| [288] | 14.7 | `2.9.32+` |  |  |  |  |  |

\* **TLS encryption**: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

```{seealso}
* [Information about all major versions](/explanation/charm-versions/index) 
* {doc}`Charmed PostgreSQL 16 releases <postgresql-16:reference/releases>`
```

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

Check the tables below, or use [`juju info`](https://juju.is/docs/juju/juju-info).
```

### Release 552-553

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS | Snap revision |
|:--------:|:-----:|:-----:|:-----:|:-----:|
|[553] | ![check] |          |  ![check]  | 143 |
|[552] |          | ![check] |  ![check]  | 142 |

<details>
<summary>Older releases</summary>

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS | Snap revision |
|:--------:|:-----:|:-----:|:-----:|:-----:|
|[468] |![check]  |          | ![check] | 120 |
|[467] |          | ![check] | ![check] | 121 |
|[430] |          | ![check] | ![check] | 114 |
|[429] |![check]  |          | ![check] | 115 |
|[363] |![check]  |          | ![check] | 96 |      
|[351] |![check]  |          | ![check] | 89 |      
|[336] |![check]  |          | ![check] | 85 |    
|[288] |![check]  |          | ![check] | 31 |    

</details>

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/reference/plugins-extensions).

<!-- LINKS-->
[553]: https://github.com/canonical/postgresql-operator/releases/tag/rev552
[552]: https://github.com/canonical/postgresql-operator/releases/tag/rev552

[468]: https://github.com/canonical/postgresql-operator/releases/tag/rev467
[467]: https://github.com/canonical/postgresql-operator/releases/tag/rev467

[430]: https://github.com/canonical/postgresql-operator/releases/tag/rev429
[429]: https://github.com/canonical/postgresql-operator/releases/tag/rev429

[363]: https://github.com/canonical/postgresql-operator/releases/tag/rev363
[351]: https://github.com/canonical/postgresql-operator/releases/tag/rev351
[336]: https://github.com/canonical/postgresql-operator/releases/tag/rev336
[288]: https://github.com/canonical/postgresql-operator/releases/tag/rev288


<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

