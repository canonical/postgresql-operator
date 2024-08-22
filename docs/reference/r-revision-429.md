>Reference > Release Notes > [All revisions](t/11875) > Revision 429/430

# Revision 429/430

<sub>June 28, 2024</sub>

Dear community,

Canonical's newest Charmed PostgreSQL operator has been published in the 14/stable [channel](https://charmhub.io/postgresql?channel=14/stable) :tada:

Due to the newly added support for `arm64` architecture, the PostgreSQL charm now releases two revisions simultaneously: 
* Revision 429 is built for `amd64`
* Revision 430 is built for for `arm64`

To make sure you deploy for the right architecture, we recommend setting an [architecture constraint](https://juju.is/docs/juju/constraint#heading--arch) for your entire juju model.

Otherwise, it can be done at deploy time with the `--constraints` flag:
```shell
juju deploy postgresql --constraints arch=<arch> 
```
where `<arch>` can be `amd64` or `arm64`.

---

## Highlights
Below are the major highlights of this release. To see all changes since the previous stable release, check the [release notes on GitHub](https://github.com/canonical/postgresql-operator/releases/tag/rev430).

* Upgraded PostgreSQL from v.14.10 → v.14.11 ([PR #432](https://github.com/canonical/postgresql-operator/pull/432))
  * Check the official [PostgreSQL release notes](https://www.postgresql.org/docs/release/14.11/)
* Added support for ARM64 architecture ([PR #381](https://github.com/canonical/postgresql-operator/pull/381))
* Added support for cross-regional asynchronous replication ([PR #452](https://github.com/canonical/postgresql-operator/pull/452)) ([DPE-2953](https://warthogs.atlassian.net/browse/DPE-2953))
  * This feature focuses on disaster recovery by distributing data across different servers. Check our [new how-to guides](https://charmhub.io/postgresql/docs/h-async-set-up) for a walkthrough of the cross-model setup, promotion, switchover, and other details.
* Added support for tracing with Tempo K8s ([PR #485](https://github.com/canonical/postgresql-operator/pull/485)) ([DPE-4616](https://warthogs.atlassian.net/browse/DPE-4616))
  * Check our new guide: [How to enable tracing](https://charmhub.io/postgresql/docs/h-enable-tracing)
* Released new [Charmed Sysbench operator](https://charmhub.io/sysbench) for easy performance testing

### Enhancements 
* Added timescaledb plugin/extension ([PR#470](https://github.com/canonical/postgresql-operator/pull/470))
  * See the [Configuration tab]((https://charmhub.io/postgresql/configuration?channel=14/candidate#plugin_timescaledb_enable)) for a full list of supported plugins/extensions
* Added incremental and differential backup support ([PR #479](https://github.com/canonical/postgresql-operator/pull/479)) ([DPE-4462](https://warthogs.atlassian.net/browse/DPE-4462))
  * Check our guide: [How to create and list backups](https://charmhub.io/postgresql/docs/h-create-backup)
* Added support for disabling the operator ([PR#412](https://github.com/canonical/postgresql-operator/pull/412)) ([DPE-2469](https://warthogs.atlassian.net/browse/DPE-2469))
* Added support for subordination with:
  * `ubuntu-advantage` ([PR#397](https://github.com/canonical/postgresql-operator/pull/397)) ([DPE-3644](https://warthogs.atlassian.net/browse/DPE-3644))
  * `landscape-client` ([PR#388](https://github.com/canonical/postgresql-operator/pull/388)) ([DPE-3644](https://warthogs.atlassian.net/browse/DPE-3644))
* Added configuration option for backup retention time ([PR#474](https://github.com/canonical/postgresql-operator/pull/474))([DPE-4401](https://warthogs.atlassian.net/browse/DPE-4401))
* Added `experimental_max_connections` config option ([PR#472](https://github.com/canonical/postgresql-operator/pull/472))
* Added check for replicas encrypted connection ([PR#437](https://github.com/canonical/postgresql-operator/pull/437))

### Bugfixes
* Fixed slow charm bootstrap time ([PR#413](https://github.com/canonical/postgresql-operator/pull/413))
* Fixed large objects ownership ([PR#349](https://github.com/canonical/postgresql-operator/pull/349))
* Fixed secrets crash for "certificates-relation-changed" after the refresh ([PR#475](https://github.com/canonical/postgresql-operator/pull/475))
* Fixed network cut tests ([PR#346](https://github.com/canonical/postgresql-operator/pull/346)) ([DPE-3257](https://warthogs.atlassian.net/browse/DPE-3257))

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-operator/issues).

For a full list of all changes in this revision, see the [GitHub Release](https://github.com/canonical/postgresql-operator/releases/tag/rev430). 

## Technical details
This section contains some technical details about the charm's contents and dependencies.  Make sure to also check the [system requirements](/t/11743).

If you are jumping over several stable revisions, check [previous release notes](/t/11875) before upgrading.

### Packaging
This charm is based on the [`charmed-postgresql` snap](https://snapcraft.io/charmed-postgresql) (pinned revision 113). It packages:
* postgresql `v.14.11`
	* [`14.11-0ubuntu0.22.04.1`](https://launchpad.net/ubuntu/+source/postgresql-14/14.11-0ubuntu0.22.04.1) 
* pgbouncer `v.1.21`
	* [`1.21.0-0ubuntu0.22.04.1~ppa1`](https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer)
* patroni `v.3.1.2 `
	* [`3.1.2-0ubuntu0.22.04.1~ppa2`](https://launchpad.net/~data-platform/+archive/ubuntu/patroni)
* pgBackRest `v.2.48`
	* [`2.48-0ubuntu0.22.04.1~ppa1`](https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest)
* prometheus-postgres-exporter `v.0.12.1`

### Libraries and interfaces
This charm revision imports the following libraries: 

* **grafana_agent `v0`** for integration with Grafana 
    * Implements  `cos_agent` interface
* **rolling_ops `v0`** for rolling operations across units 
    * Implements `rolling_op` interface
* **tempo_k8s `v1`, `v2`** for integration with Tempo charm
    * Implements `tracing` interface
* **tls_certificates_interface `v2`** for integration with TLS charms
    * Implements `tls-certificates` interface

See the [`/lib/charms` directory on GitHub](https://github.com/canonical/postgresql-operator/tree/main/lib/charms) for more details about all supported libraries.

See the [`metadata.yaml` file on GitHub](https://github.com/canonical/postgresql-operator/blob/main/metadata.yaml) for a full list of supported interfaces

## Contact us

Charmed PostgreSQL is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.  
* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-operator/issues)  
*  Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)  
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.