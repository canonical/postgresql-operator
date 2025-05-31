# PostgreSQL major versions

Charmed PostgreSQL is shipped in following [tracks](https://documentation.ubuntu.com/juju/3.6/reference/charm/#track): 

* [PostgreSQL 16] (channel `16/candidate`)
* [PostgreSQL 14] (channel `14/stable`)
* [Legacy PostgreSQL charm] (channel `latest/stable`) -> **deprecated**

This includes two major PostgreSQL versions,  `14` and `16`, matching [Ubuntu versioning](https://packages.ubuntu.com/postgresql) for PostgreSQL.

## PostgreSQL 16

PostgreSQL 16 is shipped in track `16` and is available for testing in the channel `16/candidate`.

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
* [Juju user secrets](https://documentation.ubuntu.com/juju/latest/reference/secret/index.html#user) for charm [internal passwords](/t/17692)
* [Timescale Community Edition]
* [Extended COS integration]
  * [Profiling via Parca]
  * [Tracing via Tempo]
* Improved [security hardening]
* New "juju refresh" library (Refresh v3)
* (WIP) [Improved built-in roles](/t/17725) 
* (WIP) Migrated to TLS v4 library
  * (WIP) New endpoints `client-certificates` and `peer-certificates`
  * (WIP) Endpoint `peer-interfaces` uses TLS by default

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
* The charm endpoint `certificates` has ben split into `client-certificates` and `peer-certificates`.

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

* The track `14` is in bug-fixing/support mode. New Charmed PostgreSQL `16` features will NOT be backported to track `14`.
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

[LDAP integration]: /t/17361
[SoS report integration]: /t/17228
[Recovery improvements]: /t/17523
[synchronous units]: https://charmhub.io/postgresql/configurations?channel=14/edge#synchronous_node_count
[internal charm passwords]: /t/10798
[rotation]: /t/9703
[Timescale Community Edition]: /t/17528
[Extended COS integration]: /t/10600
[Profiling via Parca]: /t/17172
[Tracing via Tempo]: /t/14521
[security hardening]: /t/16852
[Multiple Juju storage support]: /t/17529
[Juju Spaces support]: /t/17416

[release notes]: /t/11875

[Interfaces and endpoints]: /t/10251

[Deployment]: /t/16811
[Backup and restore]: /t/9683
[COS integration]: /t/10600
[TLS integration]: /t/9685
[LDAP integration]: /t/17361
[`arm64` architecture]: /t/11743

[legacy charm explanation page]: /t/10690