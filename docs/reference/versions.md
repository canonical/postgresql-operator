# PostgreSQL major versions

Charmed PostgreSQL is shipped in following [tracks](https://documentation.ubuntu.com/juju/3.6/reference/charm/#track): 

* [PostgreSQL 16] (channel `16/edge`)
* [PostgreSQL 14] (channel `14/stable`)
* [Legacy PostgreSQL charm] (channel `latest/stable`) -> **deprecated**

This includes two major PostgreSQL versions,  `14` and `16`, matching [Ubuntu versioning](https://packages.ubuntu.com/postgresql) for PostgreSQL.

## PostgreSQL 16

PostgreSQL 16 is shipped in track `16` and is available for testing in the channel `16/edge`.

>Released alongside [PgBouncer] and [Data Integrator](https://charmhub.io/data-integrator) for Ubuntu 24.04

**Base:** Noble (Ubuntu 24.04)

**Supported architectures:** `arm64` and `amd64`.

### Supported features

* [Juju Spaces support]
* [Multiple Juju storage support]
* [LDAP integration] (also supported by PostgreSQL `14`)
* [SoS report integration] (also supported by PostgreSQL `14`)
* [Recovery improvements] (also supported by PostgreSQL `14`)
  * All replicas are now [synchronous units]
  * Switchover the primary unit via `promote-to-primary scope=unit`
  * Raft re-init helper: `promote-to-primary scope=unit force=yes`
* [Juju user secrets](https://documentation.ubuntu.com/juju/latest/reference/secret/index.html#user) for charm [internal passwords]
* [Timescale Community Edition]
* [Extended COS integration]
  * [Profiling via Parca]
  * [Tracing via Tempo]
* Improved [security hardening]
* [Improved built-in roles]
* New "juju refresh" library (Refresh v3)
* Migrated to TLS v4 library
  * New endpoints `client-certificates` and `peer-certificates`
  * Endpoint `peer-interfaces` uses TLS by default

<!--
Saving the following items for release notes:
* [Released slim PostgreSQL SNAP](https://snapcraft.io/postgresql)
-->

Read more about Charmed PostgreSQL 16 features in the [release notes].

### Deprecated / removed

* Legacy interface `psql` (endpoints `db` and `db-admin`).
  * See more about supported interfaces in [Interfaces and endpoints].
* Support for Juju < `v3.6`
  * Charmed PostgreSQL 16 requires Juju `3.6+ LTS` due to [Juju secrets](https://documentation.ubuntu.com/juju/3.6/reference/secret/index.html) support. 
* Juju actions `get-password` and `set-password`.
  * For security reasons, these actions are replaced by [Juju secrets](https://documentation.ubuntu.com/juju/3.6/reference/secret/index.html).
* [Timescale Apache 2 edition](https://docs.timescale.com/about/latest/timescaledb-editions/) has been replaced by [Timescale Community edition](https://docs.timescale.com/about/latest/timescaledb-editions/). 
* The charm action `set-tls-private-key ` has been removed (will be re-introduced as Juju User Secrets)
* The charm actions `pre-upgrade-check` and `resume-upgrade ` have been removed (replaced with `pre-refresh-check` and `resume-refresh` accordingly to be consistent with `juju refresh`)
* The charm endpoint `certificates` has been split into `client-certificates` and `peer-certificates`.

## PostgreSQL 14

PostgreSQL 14 is shipped in track `14` and available for production in the channel `14/stable`. 

**Base:** Jammy (Ubuntu 22.04)

**Supported architectures:** `arm64` and `amd64`.

### Supported features

* [Deployment] on multiple cloud services
* [Backup and restore]
  * Including point-in-time recovery (PITR)
* [COS integration]
* [TLS integration]
* [LDAP integration]
* [`arm64` architecture]

Read more about Charmed PostgreSQL 14 features in the [release notes].

### Deprecated

* The track `14` is in bug-fixing/support mode. New Charmed PostgreSQL `16` features will NOT be back-ported to track `14`.
* Charmed PostgreSQL 14 ships [Timescale Apache 2 edition](https://docs.timescale.com/about/latest/timescaledb-editions/) only.

## Legacy PostgreSQL charm

The legacy charm in the track `latest`  has been deprecated and is **not supported.** It is still available here for the historical and comparative reasons only. 

Please use the supported tracks of the modern charm: `14/` and `16/`.

Learn more in the [legacy charm explanation page].

<!-- Links -->

[PostgreSQL 16]: https://charmhub.io/postgresql?channel=16/beta
[PostgreSQL 14]: https://charmhub.io/postgresql?channel=14/stable
[Legacy PostgreSQL charm]: https://charmhub.io/postgresql?channel=latest/stable

[PgBouncer]: https://charmhub.io/pgbouncer

[LDAP integration]: /how-to/enable-ldap
[SoS report integration]: /reference/troubleshooting/sos-report
[Recovery improvements]: /how-to/switchover-failover
[synchronous units]: https://charmhub.io/postgresql/configurations?channel=14/edge#synchronous_node_count
[internal charm passwords]: /explanation/users
[Timescale Community Edition]: /how-to/enable-plugins-extensions/enable-timescaledb
[Extended COS integration]: /how-to/monitoring-cos/enable-monitoring
[Profiling via Parca]: /how-to/monitoring-cos/enable-profiling
[Tracing via Tempo]: /how-to/monitoring-cos/enable-tracing
[security hardening]: /explanation/security/index
[Multiple Juju storage support]: /how-to/deploy/juju-storage
[Juju Spaces support]: /how-to/deploy/juju-spaces
[Improved built-in roles]: /explanation/roles
[release notes]: /reference/releases
[internal passwords]: /how-to/manage-passwords
[Interfaces and endpoints]: /explanation/interfaces-and-endpoints

[Deployment]: /how-to/deploy/index
[Backup and restore]: /how-to/back-up-and-restore/create-a-backup
[COS integration]: /how-to/monitoring-cos/enable-monitoring
[TLS integration]: /how-to/enable-tls
[`arm64` architecture]: /reference/system-requirements

[legacy charm explanation page]: /explanation/legacy-charm

