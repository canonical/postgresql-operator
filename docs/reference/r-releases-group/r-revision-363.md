>Reference > Release Notes > [All revisions](/t/11875) > [Revision 363](/t/13124)
# Revision 363 
<sub>February 21, 2024</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for IAAS/VM has been published in the `14/stable` [channel](https://charmhub.io/postgresql?channel=14/stable) :tada: 

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11875) before upgrading to this revision.

## Features you can start using today
* [CORE] PostgreSQL upgrade 14.9 -> 14.10. ([DPE-3217](https://warthogs.atlassian.net/browse/DPE-3217))
  * **Note**: It is advisable to REINDEX potentially-affected indexes after installing this update! (See [PostgreSQL changelog](https://changelogs.ubuntu.com/changelogs/pool/main/p/postgresql-14/postgresql-14_14.10-0ubuntu0.22.04.1/changelog))
* [CORE] Juju 3.1.7+ support ([#2037120](https://bugs.launchpad.net/juju/+bug/2037120))
* [PLUGINS] pgVector extension/plugin ([DPE-3159](https://warthogs.atlassian.net/browse/DPE-3159))
* [PLUGINS] New PostGIS plugin ([#312](https://github.com/canonical/postgresql-operator/pull/312))
* [PLUGINS] More new plugins - [50 in total](/t/10946)
* [MONITORING] COS Awesome Alert rules ([DPE-3160](https://warthogs.atlassian.net/browse/DPE-3160))
* [SECURITY] Updated TLS libraries for compatibility with new charms 
  * [manual-tls-certificates](https://charmhub.io/manual-tls-certificates)
  * [self-signed-certificates](https://charmhub.io/self-signed-certificates)
  * Any charms compatible with [ tls_certificates_interface.v2.tls_certificates](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates)
* All functionality from [previous revisions](/t/11875)

## Bugfixes

* [DPE-3199](https://warthogs.atlassian.net/browse/DPE-3199) Stabilized internal Juju secrets management
* [DPE-3258](https://warthogs.atlassian.net/browse/DPE-3258) Check system identifier in stanza (backups setup stabilization)

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-operator/issues) platforms.
[GitHub Releases](https://github.com/canonical/postgresql-operator/releases) provide a detailed list of bugfixes, PRs, and commits for each revision.

## What is inside the charms

* Charmed PostgreSQL ships the latest PostgreSQL `14.10-0ubuntu0.22.04.1`
* PostgreSQL cluster manager Patroni updated to `v.3.1.2`
* Backup tools pgBackRest updated to `v.2.48`
* The Prometheus postgres-exporter is `0.12.1-0ubuntu0.22.04.1~ppa1`
* VM charms based on [Charmed PostgreSQL](https://snapcraft.io/charmed-postgresql) SNAP (Ubuntu LTS 22.04 - `ubuntu:22.04-based`) revision 96
* Principal charms supports the latest LTS series 22.04 only
* Subordinate charms support LTS 22.04 and 20.04 only

## Technical notes

* Starting with this revision (336+), you can use `juju refresh` to upgrade Charmed PostgreSQL
* Use this operator together with modern [Charmed PgBouncer operator](https://charmhub.io/pgbouncer?channel=1/stable)
* Please check [the previously posted restrictions](/t/11875)
* Ensure [the charm requirements](/t/11743) met

## Contact us

Charmed PostgreSQL is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.
* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.