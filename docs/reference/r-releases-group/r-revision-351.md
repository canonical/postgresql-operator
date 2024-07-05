>Reference > Release Notes > [All revisions](/t/11875) > [Revision 351](/t/12823)
# Revision 351
<sub>January 3, 2024</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for IAAS/VM has been published in the `14/stable` [channel](https://charmhub.io/postgresql?channel=14/stable).

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11875) before upgrading to this revision.

## Features you can start using today

* [Core] Updated `Charmed PostgreSQL` SNAP image ([PR#291](https://github.com/canonical/postgresql-operator/pull/291))([DPE-3039](https://warthogs.atlassian.net/browse/DPE-3039)):
  * `Patroni` updated from 3.0.2 to 3.1.2
  * `Pgbackrest` updated from 2.47 to 2.48
* [Plugins] [Add 24 new plugins/extension](https://charmhub.io/postgresql/docs/r-plugins-extensions) in ([PR#251](https://github.com/canonical/postgresql-operator/pull/251))
* [Plugins] **NOTE**: extension `plpython3u` is deprecated and will be removed from [list of supported plugins](/t/10946) soon!
* [Config] [Add 29 new configuration options](https://charmhub.io/postgresql/configure) in ([PR#239](https://github.com/canonical/postgresql-operator/pull/239))([DPE-1781](https://warthogs.atlassian.net/browse/DPE-1781))
* [Config] **NOTE:** the config option `profile-limit-memory` is deprecated. Use `profile_limit_memory` (to follow the [naming conventions](https://juju.is/docs/sdk/naming))! ([PR#306](https://github.com/canonical/postgresql-operator/pull/306))([DPE-3096](https://warthogs.atlassian.net/browse/DPE-3096))
* [Charm] Add Juju Secret labels in ([PR#270](https://github.com/canonical/postgresql-operator/pull/270))([DPE-2838](https://warthogs.atlassian.net/browse/DPE-2838))
* [Charm] Update Python dependencies in ([PR#293](https://github.com/canonical/postgresql-operator/pull/293))
* [DB] Add handling of tables ownership in ([PR#298](https://github.com/canonical/postgresql-operator/pull/298))([DPE-2740](https://warthogs.atlassian.net/browse/DPE-2740))
* ([COS](https://charmhub.io/topics/canonical-observability-stack)) Moved Grafana dashboard legends to the bottom of the graph in ([PR#295](https://github.com/canonical/postgresql-operator/pull/295))([DPE-2622](https://warthogs.atlassian.net/browse/DPE-2622))
* ([COS](https://charmhub.io/topics/canonical-observability-stack)) Add Patroni COS support ([#261](https://github.com/canonical/postgresql-operator/pull/261))([DPE-1993](https://warthogs.atlassian.net/browse/DPE-1993))
* [CI/CD] Charm migrated to GitHub Data reusable workflow in ([PR#263](https://github.com/canonical/postgresql-operator/pull/263))([DPE-2789](https://warthogs.atlassian.net/browse/DPE-2789))
* All the functionality from [the previous revisions](/t/11875)

## Bugfixes

* Fixed enabling extensions when new database is created ([PR#252](https://github.com/canonical/postgresql-operator/pull/252))([DPE-2569](https://warthogs.atlassian.net/browse/DPE-2569))
* Block the charm if the legacy interface requests [roles](https://discourse.charmhub.io/t/charmed-postgresql-explanations-interfaces-endpoints/10251) ([DPE-3077](https://warthogs.atlassian.net/browse/DPE-3077))

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-operator/issues) platforms.
[GitHub Releases](https://github.com/canonical/postgresql-operator/releases) provide a detailed list of bugfixes, PRs, and commits for each revision.
## Inside the charms

* Charmed PostgreSQL ships the latest PostgreSQL “14.9-0ubuntu0.22.04.1”
* PostgreSQL cluster manager Patroni updated to "3.2.1"
* Backup tools pgBackRest updated to "2.48"
* The Prometheus postgres-exporter is "0.12.1-0ubuntu0.22.04.1~ppa1"
* VM charms based on [Charmed PostgreSQL](https://snapcraft.io/charmed-postgresql) SNAP (Ubuntu LTS “22.04” - ubuntu:22.04-based) revision 89
* Principal charms supports the latest LTS series “22.04” only
* Subordinate charms support LTS “22.04” and “20.04” only

## Technical notes

* Upgrade (`juju refresh`) is possible from this revision 336+
* Use this operator together with a modern operator "[pgBouncer](https://charmhub.io/pgbouncer?channel=1/stable)"
* Please check additionally [the previously posted restrictions](/t/11875)
* Ensure [the charm requirements](/t/11743) met

## Contact us

Charmed PostgreSQL is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.
* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.