# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://juju.is/docs/juju/channel#heading--risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases page on GitHub](https://github.com/canonical/postgresql-operator/releases).

## Dependencies and supported features

For a given release, this table shows:
* The PostgreSQL version packaged inside.
* The minimum Juju 3 version required to reliably operate **all** features of the release
   > This charm still supports older versions of Juju down to 2.9. See the [Juju section of the system requirements](/t/11743) for more details.
* Support for specific features

| Release | PostgreSQL version | Juju 3 version | [TLS encryption](/t/9685)* | [COS monitoring](/t/10600) | [Minor version upgrades](/t/12089) | [Cross-regional async replication](/t/15412) | [Point-in-time recovery](/t/9693) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| [552], [553] | 14.15 | `3.6.1+` | ![check] | ![check] | ![check] | ![check] | ![check] |
| [467], [468] | 14.12 | `3.4.3+` | ![check] | ![check] | ![check] | ![check] | ![check] |
| [429], [430] | 14.11 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] |  |
| [363] | 14.10 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] |  |
| [351] | 14.9 | `3.1.6+` |  | ![check] | ![check] |  |  |
| [336] | 14.9 | `3.1.5+` |  | ![check] | ![check] |  |  |
| [288] | 14.7 | `2.9.32+` |  |  |  |  |  |

\* **TLS encryption**: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

For more details about a particular revision, refer to its dedicated Release Notes page.
For more details about each feature/interface, refer to the documentation linked in the column header.

## Architecture and base
Several [revisions](https://juju.is/docs/sdk/revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

> If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

> If you deploy a specific revision, **you must make sure it matches your base and architecture** via the tables below or with [`juju info`](https://juju.is/docs/juju/juju-info)

### Release 552-553

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[553]  | ![check] |        |  ![check]  |
|[552] |          | ![check] |  ![check]  |

[details=Older releases]

### Release 467-468

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[468]  |![check] | | ![check]  |
|[467] |  | ![check]| ![check] |

### Release 429-430

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[430] |![check]| | ![check]   |
|[429] |  | ![check]| ![check] |

### Release 363

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[363] | ![check]| | ![check]  |


### Release 351

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[351] |![check]| | ![check]   |


### Release 336

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[336] |![check]| | ![check]   |


### Release 288

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[288] |![check]| | ![check]   |

[/details]

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/t/10946).

> **Note**: Our release notes are an ongoing work in progress. If there is any additional information about releases that you would like to see or suggestions for other improvements, don't hesitate to contact us on [Matrix ](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) or [leave a comment](https://discourse.charmhub.io/t/charmed-postgresql-reference-release-notes/11875).

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