>Reference > Release Notes > [All revisions](/t/11875) > [Revision 336](/t/11877)
# Revision 336
<sub>Wednesday, October 18, 2023</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for IAAS/VM has been published in the `14/stable` [channel](https://charmhub.io/postgresql?channel=14/stable). :tada: 

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11875) before upgrading to this revision.

## Features you can start using today
* [Add Juju 3 support](/t/11743) (Juju 2 is still supported) [[DPE-1758](https://warthogs.atlassian.net/browse/DPE-1758)]
* All secrets are now stored in [Juju secrets](https://juju.is/docs/juju/manage-secrets) [[DPE-1758](https://warthogs.atlassian.net/browse/DPE-1758)]
* Charm [minor upgrades](/t/12089) and [minor rollbacks](/t/12090) [[DPE-1767](https://warthogs.atlassian.net/browse/DPE-1767)]
* [Canonical Observability Stack (COS)](https://charmhub.io/topics/canonical-observability-stack) support [[DPE-1775](https://warthogs.atlassian.net/browse/DPE-1775)]
* [PostgreSQL plugins support](/t/10906) [[DPE-1373](https://warthogs.atlassian.net/browse/DPE-1373)]
* [Profiles configuration](/t/11974) support [[DPE-2655](https://warthogs.atlassian.net/browse/DPE-2655)]
* [Logs rotation](/t/12099) [[DPE-1754](https://warthogs.atlassian.net/browse/DPE-1754)]
* Workload updated to [PostgreSQL 14.9](https://www.postgresql.org/docs/14/release-14-9.html) [[PR#18](https://github.com/canonical/charmed-postgresql-snap/pull/18)]
* Add '`admin`' [extra user role](https://github.com/canonical/postgresql-operator/pull/199) [[DPE-2167](https://warthogs.atlassian.net/browse/DPE-2167)]
* New charm '[PostgreSQL Test App](https://charmhub.io/postgresql-test-app)'
* New documentation:
  * [Architecture (HLD/LLD)](/t/11857)
  * [Upgrade section](/t/12086)
  * [Release Notes](/t/11875)
  * [Requirements](/t/11743)
  * [Profiles](/t/11974)
  * [Users](/t/10798)
  * [Logs](/t/12099)
  * [Statuses](/t/10844)
  * [Development](/t/11862)
  * [Testing reference](/t/11773)
  * [Legacy charm](/t/10690)
  * [Plugins/extensions](/t/10906), [supported](/t/10946)
  * [Juju 2.x vs 3.x hints](/t/11985)
  * [Contacts](/t/11863)
* All the functionality from [the previous revisions](/t/11875)

## Bugfixes
* [DPE-1624](https://warthogs.atlassian.net/browse/DPE-1624), [DPE-1625](https://warthogs.atlassian.net/browse/DPE-1625)  Backup/restore fixes
* [DPE-1926](https://warthogs.atlassian.net/browse/DPE-1926) Remove fallback_application_name field from relation data
* [DPE-1712](https://warthogs.atlassian.net/browse/DPE-1712) Enabled the user to fix network issues and rerun stanza related hooks
* [DPE-2173](https://warthogs.atlassian.net/browse/DPE-2173) Fix allowed units relation data field
* [DPE-2127](https://warthogs.atlassian.net/browse/DPE-2127) Fixed databases access
* [DPE-2341](https://warthogs.atlassian.net/browse/DPE-2341) Populate extensions in unit databag
* [DPE-2218](https://warthogs.atlassian.net/browse/DPE-2218) Update charm libs to get s3 relation fix
* [DPE-1210](https://warthogs.atlassian.net/browse/DPE-1210), [DPE-2330](https://warthogs.atlassian.net/browse/DPE-2330), [DPE-2212](https://warthogs.atlassian.net/browse/DPE-2212) Open port (ability to expose charm)
* [DPE-2614](https://warthogs.atlassian.net/browse/DPE-2614) Split stanza create and stanza check
* [DPE-2721](https://warthogs.atlassian.net/browse/DPE-2721) Allow network access for pg_dump, pg_dumpall and pg_restore
* [DPE-2717](https://warthogs.atlassian.net/browse/DPE-2717) Copy dashboard changes from K8s and use the correct topology dispatcher
* [MISC] Copy fixes of DPE-2626 and DPE-2627 from k8s
* [MISC] Don't fail if the unit is already missing 
* [MISC] More resilient topology observer

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-operator/issues) platforms.

[GitHub Releases](https://github.com/canonical/postgresql-operator/releases) provide a detailed list of bugfixes, PRs, and commits for each revision.

## Inside the charms

* Charmed PostgreSQL ships the latest PostgreSQL “14.9-0ubuntu0.22.04.1”
* PostgreSQL cluster manager Patroni updated to "3.0.2"
* Backup tools pgBackRest updated to "2.47"
* The Prometheus postgres-exporter is "0.12.1-0ubuntu0.22.04.1~ppa1"
* VM charms based on [Charmed PostgreSQL](https://snapcraft.io/charmed-postgresql) SNAP (Ubuntu LTS “22.04” - ubuntu:22.04-based)
* Principal charms supports the latest LTS series “22.04” only.
* Subordinate charms support LTS “22.04” and “20.04” only.

## Technical notes

* `juju refresh` from the old-stable revision 288 to the current-revision 324 is **NOT** supported!!!<br/>The [upgrade](/t/12086) functionality is new and supported for revision 324+ only!
* Please check additionally [the previously posted restrictions](/t/11876).
* Ensure [the charm requirements](/t/11743) met

## Contact us

Charmed PostgreSQL is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.