>Reference > Release Notes > [All revisions] > Revision 517/518

[note type=caution]
This page is a work in progress for a **future release**. Please revisit at a later date!
[/note]

# Revision 517/518
<sub><TODO></sub>

Canonical's newest Charmed PostgreSQL operator has been published in the [14/stable channel].

Due to the newly added support for `arm64` architecture, the PostgreSQL charm now releases multiple revisions simultaneously:
* Revision <TODO> is built for `amd64` on Ubuntu 22.04 LTS
* Revision <TODO> is built for `arm64` on Ubuntu 22.04 LTS

> See also: [How to perform a minor upgrade]

### Contents
* [Highlights](#highlights)
* [Features and improvements](#features-and-improvements)
* [Bugfixes and maintenance](#bugfixes-and-maintenance)
* [Known limitations](#known-limitations)
* [Requirements and compatibility](#requirements-and-compatibility)
  * [Integration tests](#integration-tests)
  * [Packaging](#packaging)
---

## Highlights 

* Added timeline management to point-in-time recovery (PITR) ([PR #629](https://github.com/canonical/postgresql-operator/pull/629)) ([DPE-5561](https://warthogs.atlassian.net/browse/DPE-5561))
* Added pgAudit plugin/extension ([PR #612](https://github.com/canonical/postgresql-operator/pull/612)) ([DPE-5248](https://warthogs.atlassian.net/browse/DPE-5248))
* Observability stack (COS) improvements
  *  Polished built-in Grafana dashboard ([PR #646](https://github.com/canonical/postgresql-operator/pull/646))
  * Improved COS alert rule descriptions ([PR #651](https://github.com/canonical/postgresql-operator/pull/651)) ([DPE-5658](https://warthogs.atlassian.net/browse/DPE-5658))
* Added fully-featured terraform module ([PR #643](https://github.com/canonical/postgresql-operator/pull/643))
* Several S3 improvements ([PR #642](https://github.com/canonical/postgresql-operator/pull/642))

## Features and improvements
* Split PITR backup test in AWS and GCP ([PR #605](https://github.com/canonical/postgresql-operator/pull/605)) ([DPE-5181](https://warthogs.atlassian.net/browse/DPE-5181))
* Removed patching of private ops class. ([PR #617](https://github.com/canonical/postgresql-operator/pull/617))
* Switched charm libs from `tempo_k8s` to `tempo_coordinator_k8s` and relay tracing traffic through `grafana-agent` ([PR #640](https://github.com/canonical/postgresql-operator/pull/640))
* Implemented more meaningful group naming for multi-group tests ([PR #625](https://github.com/canonical/postgresql-operator/pull/625))
* Ignoring alias error in case alias is already existing ([PR #637](https://github.com/canonical/postgresql-operator/pull/637))
* Stopped tracking channel for held snaps ([PR #638](https://github.com/canonical/postgresql-operator/pull/638))
* Added pgBackRest logrotate configuration ([PR #645](https://github.com/canonical/postgresql-operator/pull/645)) ([DPE-5601](https://warthogs.atlassian.net/browse/DPE-5601))
* Grant priviledges to non-public schemas ([PR #647](https://github.com/canonical/postgresql-operator/pull/647)) ([DPE-5387](https://warthogs.atlassian.net/browse/DPE-5387))
* Added `tls` and `tls-ca` fields to databag ([PR #666](https://github.com/canonical/postgresql-operator/pull/666))
* Merged `update_tls_flag` into `update_endpoints` ([PR #669](https://github.com/canonical/postgresql-operator/pull/669))
* Made tox commands resilient to white-space paths ([PR #678](https://github.com/canonical/postgresql-operator/pull/678)) ([DPE-6042](https://warthogs.atlassian.net/browse/DPE-6042))
* Added microceph (local backup) integration test + bump snap version ([PR #633](https://github.com/canonical/postgresql-operator/pull/633)) ([DPE-5386](https://warthogs.atlassian.net/browse/DPE-5386))

## Bugfixes and maintenance
* Added warning logs to Patroni reinitialisation ([PR #660](https://github.com/canonical/postgresql-operator/pull/660))
* Fixed some `postgresql.conf` parameters for hardening ([PR #621](https://github.com/canonical/postgresql-operator/pull/621)) ([DPE-5512](https://warthogs.atlassian.net/browse/DPE-5512))
* Fixed lib check ([PR #627](https://github.com/canonical/postgresql-operator/pull/627))

[details=Libraries, testing, and CI]
* Data Interafces v40 ([PR #615](https://github.com/canonical/postgresql-operator/pull/615)) ([DPE-5306](https://warthogs.atlassian.net/browse/DPE-5306))
* Bump libs and remove TestCase ([PR #622](https://github.com/canonical/postgresql-operator/pull/622))
* Run tests against juju 3.6 on a nightly schedule ([PR #601](https://github.com/canonical/postgresql-operator/pull/601)) ([DPE-4977](https://warthogs.atlassian.net/browse/DPE-4977))
* Test against juju 3.6/candidate + upgrade dpw to v23.0.5 ([PR #675](https://github.com/canonical/postgresql-operator/pull/675))
* Lock file maintenance Python dependencies ([PR #644](https://github.com/canonical/postgresql-operator/pull/644))
* Migrate config .github/renovate.json5 ([PR #673](https://github.com/canonical/postgresql-operator/pull/673))
* Switch from tox build wrapper to charmcraft.yaml overrides ([PR #626](https://github.com/canonical/postgresql-operator/pull/626))
* Update canonical/charming-actions action to v2.6.3 ([PR #608](https://github.com/canonical/postgresql-operator/pull/608))
* Update codecov/codecov-action action to v5 ([PR #674](https://github.com/canonical/postgresql-operator/pull/674))
* Update data-platform-workflows to v23.0.5 ([PR #676](https://github.com/canonical/postgresql-operator/pull/676))
* Update dependency cryptography to v43.0.1 [SECURITY] ([PR #614](https://github.com/canonical/postgresql-operator/pull/614))
* Update dependency ubuntu to v24 ([PR #631](https://github.com/canonical/postgresql-operator/pull/631))
* Update Juju agents ([PR #634](https://github.com/canonical/postgresql-operator/pull/634))
* Bump libs ([PR #677](https://github.com/canonical/postgresql-operator/pull/677))
* Increase linting rules ([PR #649](https://github.com/canonical/postgresql-operator/pull/649)) ([DPE-5324](https://warthogs.atlassian.net/browse/DPE-5324))
[/details]

## Known limitations
...
<TODO>

## Requirements and compatibility
* (no change) Minimum Juju 2 version: `v.2.9.49`
* (no change) Minimum Juju 3 version: `v.3.4.3`

See the [system requirements] for more details about Juju versions and other software and hardware prerequisites.

### Integration tests
Below are some of the charm integrations tested with this revision on different Juju environments and architectures:
* Juju `v.2.9.51` on `amd64`
* Juju  `v.3.4.6` on `amd64` and `arm64`

|  Software | Revision | Tested on | |
|-----|-----|----|---|
| [postgresql-test-app] | `rev 281` | ![juju-2_amd64] ![juju-3_amd64] |
|   | `rev 279` | ![juju-2_amd64] ![juju-3_amd64]  |
|   | `rev 280` | ![juju-3_arm64] |
|   | `rev 278` | ![juju-3_arm64] |
| [data-integrator] | `rev 41` | ![juju-2_amd64] ![juju-3_amd64] |
|   | `rev 40` | ![juju-3_arm64] |
| [nextcloud] | `rev 26` | ![juju-2_amd64] ![juju-3_amd64]  | |
| [s3-integrator] | `rev 77` |  ![juju-2_amd64] ![juju-3_amd64]  |
|   | `rev 78` | ![juju-3_arm64]  |
| [tls-certificates-operator] | `rev 22` | ![juju-2_amd64] |
| [self-signed-certificates] | `rev 155` |  ![juju-3_amd64]  |
|  | `rev 205` | ![juju-3_arm64] |
| [mailman3-core] | `rev 18` | ![juju-2_amd64] ![juju-3_amd64] ![juju-3_arm64] |
| [landscape-client] | `rev 70` | ![juju-2_amd64] ![juju-3_amd64] ![juju-3_arm64]  |
| [ubuntu-advantage] | `rev 137` |  ![juju-2_amd64] ![juju-3_amd64] |
|   | `rev 139` | ![juju-3_arm64]|

See the [`/lib/charms` directory on GitHub] for details about all supported libraries.

See the [`metadata.yaml` file on GitHub] for a full list of supported interfaces.

### Packaging
This charm is based on the Charmed PostgreSQL [snap revision 132/133](https://github.com/canonical/charmed-postgresql-snap/tree/rev121). It packages:
* [postgresql] `v.14.12`
* [pgbouncer] `v.1.21`
* [patroni] `v.3.1.2 `
* [pgBackRest] `v.2.53`
* [prometheus-postgres-exporter] `v.0.12.1`

<!-- LINKS -->
[14/stable channel]: https://charmhub.io/postgresql?channel=14/stable

[All revisions]: /t/11875
[system requirements]: /t/11743
[How to perform a minor upgrade]: /t/12089

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
[landscape-client]: https://charmhub.io/landscape-client
[ubuntu-advantage]: https://charmhub.io/ubuntu-advantage

[`/lib/charms` directory on GitHub]: https://github.com/canonical/postgresql-operator/tree/rev518/lib/charms
[`metadata.yaml` file on GitHub]: https://github.com/canonical/postgresql-operator/blob/rev518/metadata.yaml

[postgresql]: https://launchpad.net/ubuntu/+source/postgresql-14/
[pgbouncer]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer
[patroni]: https://launchpad.net/~data-platform/+archive/ubuntu/patroni
[pgBackRest]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest
[prometheus-postgres-exporter]: https://launchpad.net/~data-platform/+archive/ubuntu/postgres-exporter

[juju-2_amd64]: https://img.shields.io/badge/Juju_2.9.51-amd64-darkgreen?labelColor=ea7d56 
[juju-3_amd64]: https://img.shields.io/badge/Juju_3.4.6-amd64-darkgreen?labelColor=E95420 
[juju-3_arm64]: https://img.shields.io/badge/Juju_3.4.6-arm64-blue?labelColor=E95420