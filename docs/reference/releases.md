# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases page on GitHub](https://github.com/canonical/postgresql-operator/releases).

## Dependencies and supported features

For a given release, this table shows:
* The PostgreSQL 14 version packaged inside.
* The minimum Juju 3 version required to reliably operate **all** features of the release
   > This charm still supports older versions of Juju down to 2.9. See the [Juju section of the system requirements](/reference/system-requirements) for more details.
* Support for specific features

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

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

Check the tables below, or use [`juju info`](https://juju.is/docs/juju/juju-info).
```

### Release 552-553

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[553] | ![check] |          |  ![check]  |
|[552] |          | ![check] |  ![check]  |

<details>
<summary>Older releases</summary>

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[468] |![check]  |          | ![check] |
|[467] |          | ![check] | ![check] |
|[430] |          | ![check] | ![check] |
|[429] |![check]  |          | ![check] |
|[363] |![check]  |          | ![check] |          
|[351] |![check]  |          | ![check] |         
|[336] |![check]  |          | ![check] |       
|[288] |![check]  |          | ![check] |       

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

