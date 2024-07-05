>Reference > Release Notes > [All revisions](t/11875) > Revision 429/430

# Revision 429/430

<sub>June 28, 2024</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator has been published in the 14/stable [channel](https://charmhub.io/postgresql?channel=14/stable) :tada: :

|   |AMD64|ARM64|
|---:|:---:|:---:|
| Revisions: | 429 | 430 |

[note]
If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11875) before upgrading to this revision.
[/note]  

## Features you can start using today

* [PostgreSQL upgrade 14.10 → 14.11](https://www.postgresql.org/docs/release/14.11/) [[PR#432](https://github.com/canonical/postgresql-operator/pull/432)]
  * [check official PostgreSQL release notes!](https://www.postgresql.org/docs/release/14.11/)
* [New ARM support!](https://charmhub.io/postgresql/docs/r-requirements) [[PR#381](https://github.com/canonical/postgresql-operator/pull/381)]
* [Add cross-region async replication!](https://charmhub.io/postgresql/docs/h-async-setup) [[PR#452](https://github.com/canonical/postgresql-operator/pull/452)][[DPE-2953](https://warthogs.atlassian.net/browse/DPE-2953)]
* [Add timescaledb plugin/extension](https://charmhub.io/postgresql/configuration?channel=14/candidate#plugin_timescaledb_enable) [[PR#470](https://github.com/canonical/postgresql-operator/pull/470)]
* [Add Incremental+Differential backup support](/t/9683) [[PR#479](https://github.com/canonical/postgresql-operator/pull/479)][[DPE-4462](https://warthogs.atlassian.net/browse/DPE-4462)] 
* [Easy performance testing with sysbench](https://charmhub.io/sysbench)
* [Add COS Tempo tracing support](/t/14521) [[PR#485](https://github.com/canonical/postgresql-operator/pull/485)][DPE-4616](https://warthogs.atlassian.net/browse/DPE-4616)]
* Internal disable operator mode [[PR#412](https://github.com/canonical/postgresql-operator/pull/412)][[DPE-2469](https://warthogs.atlassian.net/browse/DPE-2469)]
* Support for subordination with `ubuntu-advantage` [[PR#397](https://github.com/canonical/postgresql-operator/pull/397)][[DPE-3644](https://warthogs.atlassian.net/browse/DPE-3644)]
* Support for subordination with `landscape-client` [[PR#388](https://github.com/canonical/postgresql-operator/pull/388)][[DPE-3644](https://warthogs.atlassian.net/browse/DPE-3644)]
* Add retention time for backups [[PR#474](https://github.com/canonical/postgresql-operator/pull/474)][[DPE-4401](https://warthogs.atlassian.net/browse/DPE-4401)]
* Add `experimental_max_connections` charm config option [[PR#472](https://github.com/canonical/postgresql-operator/pull/472)]
* All the functionality from [previous revisions](https://charmhub.io/postgresql/docs/r-releases)

## Bugfixes

* [DPE-3882] Speed up charm bootstrap 2-3 times in [PR#413](https://github.com/canonical/postgresql-operator/pull/413)
* [DPE-3544] Fixed large objects ownership in [PR#349](https://github.com/canonical/postgresql-operator/pull/349)
* [DPE-3257] Fixed network cut tests in [PR#346](https://github.com/canonical/postgresql-operator/pull/346)
* [DPE-3202] Architecture-specific snap revision in [PR#345](https://github.com/canonical/postgresql-operator/pull/345)
* [DPE-3380] Handle S3 relation in primary non-leader unit in [PR#340](https://github.com/canonical/postgresql-operator/pull/340)
* [DPE-3559] Stabilise restore cluster test in [PR#351](https://github.com/canonical/postgresql-operator/pull/351)
* [DPE-3591] Fixed shared buffers validation in [PR#361](https://github.com/canonical/postgresql-operator/pull/361)
* [DPE-4068] Finished test migration from unittest to pytest + reenable secrets [PR#451](https://github.com/canonical/postgresql-operator/pull/451)
* [DPE-4106] Test legacy and modern endpoints simultaneously in [PR#396](https://github.com/canonical/postgresql-operator/pull/396)
* [DPE-2674] Convert `test_charm.py` to pytest style testing instead of unit test in [PR#425](https://github.com/canonical/postgresql-operator/pull/425)
* [DPE-3895] Handle get patroni health exception in [PR#421](https://github.com/canonical/postgresql-operator/pull/421)
* [DPE-3593] Only check config values against the DB in `on_config_changed` in [PR#395](https://github.com/canonical/postgresql-operator/pull/395)
* [DPE-3422] Switch to self-signed certificates in [PR#336](https://github.com/canonical/postgresql-operator/pull/336)
* [DPE-4336] Reset active status when removing extensions dependency block in [PR#467](https://github.com/canonical/postgresql-operator/pull/467)
* [DPE-4416] Fixed secrets crash for "certificates-relation-changed" after the refresh in [PR#475](https://github.com/canonical/postgresql-operator/pull/475)
* [DPE-4416] Fetch charm libs to the latest LIBPATCH (dp-libs v36) in [PR#475](https://github.com/canonical/postgresql-operator/pull/475)
* [DPE-4412] Use TLS CA chain for backups in [PR#484](https://github.com/canonical/postgresql-operator/pull/484)
* [DPE-4032] Stop exposing passwords on postgresql SQL queries logging in [PR#495](https://github.com/canonical/postgresql-operator/pull/495)
* [DPE-4453] Fix scale up with S3 and TLS relations in [PR#480](https://github.com/canonical/postgresql-operator/pull/480)
* [DPE-4598] Handle upgrade of top of the stack Juju leader in [PR#492](https://github.com/canonical/postgresql-operator/pull/492)
* [DPE-4416] Update rolling-ops lib to version 0.7 in [PR#478](https://github.com/canonical/postgresql-operator/pull/478)
* [MISC] Suppress oversee users in standby clusters in [PR#507](https://github.com/canonical/postgresql-operator/pull/507)
* [MISC] Updated snap, charm libs and switch away from psycopg2-binary in [PR#372](https://github.com/canonical/postgresql-operator/pull/372)
* Added check for replicas encrypted connection in [PR#437](https://github.com/canonical/postgresql-operator/pull/437)
* Updated `test_landscape_scalable_bundle_db` test in [PR#378](https://github.com/canonical/postgresql-operator/pull/378)

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-operator/issues) platforms.  
[GitHub Releases](https://github.com/canonical/postgresql-operator/releases) provide a detailed list of bugfixes, PRs, and commits for each revision.  

## Inside the charms

* Charmed PostgreSQL ships the latest PostgreSQL `14.11-0ubuntu0.22.04.1`
* PostgreSQL cluster manager Patroni updated to `3.1.2`
* Backup tools pgBackRest updated to `2.48`
* The Prometheus postgres-exporter is `0.12.1-0ubuntu0.22.04.1~ppa1`
* VM charms based on [Charmed PostgreSQL](https://snapcraft.io/charmed-postgresql) SNAP (Ubuntu LTS `22.04.4`) revision `113`
* Principal charms supports the latest LTS series 22.04 only

## Technical notes

* Upgrade via `juju refresh` is possible from revision 336+
* Use this operator together with the modern [Charmed PgBouncer operator](https://charmhub.io/pgbouncer?channel=1/stable)
* Please check [previously posted restrictions](https://charmhub.io/postgresql/docs/r-releases)  
* Ensure [the charm requirements](/t/11743) met

## Contact us

Charmed PostgreSQL is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.  
* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-operator/issues)  
*  Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)  
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.