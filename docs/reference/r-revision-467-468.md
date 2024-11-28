>Reference > Release Notes > [All revisions] > Revision 467/468

# Revision 467/468
<sub>September 11, 2024</sub>

Canonical's newest Charmed PostgreSQL operator has been published in the [14/stable channel].

Due to the newly added support for `arm64` architecture, the PostgreSQL charm now releases multiple revisions simultaneously:
* Revision 468 is built for `amd64` on Ubuntu 22.04 LTS
* Revision 467 is built for `arm64` on Ubuntu 22.04 LTS

To make sure you deploy for the right architecture, we recommend setting an [architecture constraint](https://juju.is/docs/juju/constraint#heading--arch) for your entire juju model.

Otherwise, it can be done at deploy time with the `--constraints` flag:
```shell
juju deploy postgresql --constraints arch=<arch> 
```
where `<arch>` can be `amd64` or `arm64`.

---

## Highlights 
* Upgraded PostgreSQL from v.14.11 → v.14.12 ([PR #530](https://github.com/canonical/postgresql-operator/pull/530))
  * Check the official [PostgreSQL release notes](https://www.postgresql.org/docs/release/14.12/)
* Added support for Point In Time Recovery ([PR #391](https://github.com/canonical/postgresql-operator/pull/391)) ([DPE-2582](https://warthogs.atlassian.net/browse/DPE-2582))
* Secure Syncobj and Patroni with passwords ([PR #596](https://github.com/canonical/postgresql-operator/pull/596)) ([DPE-5269](https://warthogs.atlassian.net/browse/DPE-5269))
* Removed deprecated config option `profile-limit-memory` ([PR #564](https://github.com/canonical/postgresql-operator/pull/564)) ([DPE-4889](https://warthogs.atlassian.net/browse/DPE-4889))

## Features 

* Added URI connection string to relations ([PR #527](https://github.com/canonical/postgresql-operator/pull/527)) ([DPE-2278](https://warthogs.atlassian.net/browse/DPE-2278))
* Improve `list-backups` action output ([PR #522](https://github.com/canonical/postgresql-operator/pull/522)) ([DPE-4479](https://warthogs.atlassian.net/browse/DPE-4479))
* Show start/end time in UTC time in list-backups output ([PR #551](https://github.com/canonical/postgresql-operator/pull/551))
* Switched to constant snap locales ([PR #559](https://github.com/canonical/postgresql-operator/pull/559)) ([DPE-4198](https://warthogs.atlassian.net/browse/DPE-4198))
* Moved URI generation to update endpoints ([PR #584](https://github.com/canonical/postgresql-operator/pull/584))

## Bugfixes

* Wait for exact number of units after scale down ([PR #565](https://github.com/canonical/postgresql-operator/pull/565)) ([DPE-5029](https://warthogs.atlassian.net/browse/DPE-5029))
* Improved test stability by pausing Patroni in the TLS test ([PR #534](https://github.com/canonical/postgresql-operator/pull/534)) ([DPE-4533](https://warthogs.atlassian.net/browse/DPE-4533))
* Block charm if it detects objects dependent on disabled plugins ([PR #560](https://github.com/canonical/postgresql-operator/pull/560)) ([DPE-4967](https://warthogs.atlassian.net/browse/DPE-4967))
* Disabled pgBackRest service initialization ([PR #530](https://github.com/canonical/postgresql-operator/pull/530)) ([DPE-4345](https://warthogs.atlassian.net/browse/DPE-4345))
* Increased timeout and terminate processes that are still up ([PR #514](https://github.com/canonical/postgresql-operator/pull/514)) ([DPE-4532](https://warthogs.atlassian.net/browse/DPE-4532))
* Fixed GCP backup test ([PR #521](https://github.com/canonical/postgresql-operator/pull/521)) ([DPE-4820](https://warthogs.atlassian.net/browse/DPE-4820))
* Handled on start secret exception and remove stale test ([PR #550](https://github.com/canonical/postgresql-operator/pull/550))
* Removed block on failure to get the db version ([PR #578](https://github.com/canonical/postgresql-operator/pull/578)) ([DPE-3562](https://warthogs.atlassian.net/browse/DPE-3562))
* Updated unit tests after fixing GCP backup test ([PR #528](https://github.com/canonical/postgresql-operator/pull/528)) ([DPE-4820](https://warthogs.atlassian.net/browse/DPE-4820))
* Ported some `test_self_healing` CI fixes + update check for invalid extra user credentials ([PR #546](https://github.com/canonical/postgresql-operator/pull/546)) ([DPE-4856](https://warthogs.atlassian.net/browse/DPE-4856))
* Fixed slow bootstrap of replicas ([PR #510](https://github.com/canonical/postgresql-operator/pull/510)) ([DPE-4759](https://warthogs.atlassian.net/browse/DPE-4759))
* Fixed conditional password ([PR #604](https://github.com/canonical/postgresql-operator/pull/604))
* Added enforcement of Juju versions ([PR #518](https://github.com/canonical/postgresql-operator/pull/518)) ([DPE-4809](https://warthogs.atlassian.net/browse/DPE-4809))
* Fixed a missing case for peer to secrets translation. ([PR #533](https://github.com/canonical/postgresql-operator/pull/533))
* Updated README.md ([PR #538](https://github.com/canonical/postgresql-operator/pull/538)) ([DPE-4901](https://warthogs.atlassian.net/browse/DPE-4901))
* Increased test coverage ([PR #505](https://github.com/canonical/postgresql-operator/pull/505))

## Known limitations

 * The unit action `resume-upgrade` randomly raises a [harmless error message](https://warthogs.atlassian.net/browse/DPE-5420): `terminated`.
 * The [charm sysbench](https://charmhub.io/sysbench) may [crash](https://warthogs.atlassian.net/browse/DPE-5436) during a PostgreSQL charm refresh.
 * Make sure that [cluster-cluster replication](/t/13991) is requested for the same charm/workload revisions. An automated check is [planned](https://warthogs.atlassian.net/browse/DPE-5418).
 * [Contact us](/t/11863) to schedule [the cluster-cluster replication](/t/13991) upgrade with you.

If you are jumping over several stable revisions, check [previous release notes][All revisions] before upgrading.

## Requirements and compatibility

This charm revision features the following changes in dependencies:
* (increased) The minimum Juju version required to reliably operate **all** features of the release is `v3.4.5`
  > You can upgrade to this revision on Juju  `v2.9.50+`, but it will not support newer features like cross-regional asynchronous replication, point-in-time recovery, and modern TLS certificate charm integrations.
* (increased) PostgreSQL version 14.12

Check the [system requirements] page for more details, such as supported minor versions of Juju and hardware requirements.

### Integration tests
Below are the charm integrations tested with this revision on different Juju environments and architectures:
* Juju `v.2.9.50` on `amd64`
* Juju  `v.3.4.5` on `amd64` and `arm64`

| Software | Version | Notes |
|-----|-----|-----|
| [lxd] | `5.12/stable` | |
| [nextcloud] | `v29.0.5.1`, `rev 26` | |
| [mailman3-core] | `rev 18` | |
| [data-integrator] | `rev 41` | |
| [s3-integrator] | `rev 31` | |
| [postgresql-test-app] | `rev 237` | |

See the [`/lib/charms` directory on GitHub] for details about all supported libraries.

See the [`metadata.yaml` file on GitHub] for a full list of supported interfaces.

### Packaging

This charm is based on the Charmed PostgreSQL [snap Revision 120/121]. It packages:
* [postgresql `v.14.12`]
* [pgbouncer `v.1.21`]
* [patroni `v.3.1.2 `]
* [pgBackRest `v.2.48`]
* [prometheus-postgres-exporter `v.0.12.1`]

### Dependencies and automations

[details=This section contains a list of updates to libs, dependencies, actions, and workflows.] 

* Added jinja2 as a dependency ([PR #520](https://github.com/canonical/postgresql-operator/pull/520)) ([DPE-4816](https://warthogs.atlassian.net/browse/DPE-4816))
* Switched Jira issue sync from workflow to bot ([PR #586](https://github.com/canonical/postgresql-operator/pull/586))
* Updated canonical/charming-actions action to v2.6.2 ([PR #523](https://github.com/canonical/postgresql-operator/pull/523))
* Updated data-platform-workflows to v21.0.1 ([PR #599](https://github.com/canonical/postgresql-operator/pull/599))
* Updated dependency cryptography to v43 ([PR #539](https://github.com/canonical/postgresql-operator/pull/539))
* Updated dependency tenacity to v9 ([PR #558](https://github.com/canonical/postgresql-operator/pull/558))
* Updated Juju agents (patch) ([PR #553](https://github.com/canonical/postgresql-operator/pull/553))
* Switch to resusable presets ([PR #513](https://github.com/canonical/postgresql-operator/pull/513))
* Use poetry package-mode=false ([PR #556](https://github.com/canonical/postgresql-operator/pull/556))
* Switched test-app interface ([PR #557](https://github.com/canonical/postgresql-operator/pull/557))
[/details]

<!-- DISCOURSE TOPICS-->
[All revisions]: /t/11875
[system requirements]: /t/11743

<!-- CHARM GITHUB -->
[`/lib/charms` directory on GitHub]: https://github.com/canonical/postgresql-operator/tree/rev468/lib/charms
[`metadata.yaml` file on GitHub]: https://github.com/canonical/postgresql-operator/blob/rev468/metadata.yaml

<!-- CHARMHUB -->
[14/stable channel]: https://charmhub.io/postgresql?channel=14/stable

<!-- SNAP/ROCK-->
[`charmed-postgresql` packaging]: https://github.com/canonical/charmed-postgresql-snap
[snap Revision 120/121]: https://github.com/canonical/charmed-postgresql-snap/releases/tag/rev121
[rock image]: ghcr.io/canonical/charmed-postgresql@sha256:7ef86a352c94e2a664f621a1cc683d7a983fd86e923d98c32b863f717cb1c173 

[postgresql `v.14.12`]: https://launchpad.net/ubuntu/+source/postgresql-14/14.12-0ubuntu0.22.04.1
[pgbouncer `v.1.21`]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer
[patroni `v.3.1.2 `]: https://launchpad.net/~data-platform/+archive/ubuntu/patroni
[pgBackRest `v.2.48`]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest
[prometheus-postgres-exporter `v.0.12.1`]: https://launchpad.net/~data-platform/+archive/ubuntu/postgres-exporter

<!-- EXTERNAL LINKS -->
[juju]: https://juju.is/docs/juju/
[lxd]: https://documentation.ubuntu.com/lxd/en/latest/
[nextcloud]: https://charmhub.io/nextcloud
[mailman3-core]: https://charmhub.io/mailman3-core
[data-integrator]: https://charmhub.io/data-integrator
[s3-integrator]: https://charmhub.io/s3-integrator
[postgresql-test-app]: https://charmhub.io/postgresql-test-app
[discourse-k8s]: https://charmhub.io/discourse-k8s
[indico]: https://charmhub.io/indico
[microk8s]: https://charmhub.io/microk8s
[tls-certificates-operator]: https://charmhub.io/tls-certificates-operator
[self-signed-certificates]: https://charmhub.io/self-signed-certificates

<!-- BADGES (unused) -->
[amd64]: https://img.shields.io/badge/amd64-darkgreen
[arm64]: https://img.shields.io/badge/arm64-blue