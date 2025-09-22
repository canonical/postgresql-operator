# Modern PostgreSQL charm

Modern PostgreSQL charms are [Ops charms](https://documentation.ubuntu.com/juju/3.6/reference/charm/#ops-charm) released in the Charmhub channels `14/` and `16/`. 

Both modern charms provide the `database` endpoint for the `postgresql_client` interface.

PostgreSQL 14 (track `14/`) additionally provides `db` and `db-admin` endpoints for the legacy `pgsql` interface, and supports migration from the legacy charm.

PostgreSQL 16 (track `16/`) does not provide legacy endpoints.

```{note}
You are currently viewing the documentation for **Charmed PostgreSQL 14**.

To switch between versions, use the small rectangular menu at the bottom right corner of the page.
```

## PostgreSQL 16

**Latest stable version** with active feature development.

* **Base:** Ubuntu 24.04 LTS (Noble)
* **Supported architectures:** `amd64`, `arm64`
* **Channel:** `16/stable` (latest development available for testing in `16/edge`)
* **Juju version:** Requires Juju 3.6+ LTS
* **Support status:** ![check] Active development and full support

### New features

* [**Juju spaces**](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/how-to/deploy/juju-spaces) - Enhanced networking capabilities for complex deployment scenarios
* [**Juju user secrets**](https://documentation.ubuntu.com/juju/latest/reference/secret/index.html#user-secret) - Secure management of the charm's [internal passwords](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/how-to/manage-passwords)
* **Improved** [**security hardening**](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/explanation/security) - Enhanced security posture and best practices
* **TLS v4 library migration**
  * New endpoints `client-certificates` and `peer-certificates` 
  * Endpoint `peer-interfaces` uses TLS by default
  * See all endpoints on [Charmhub](https://charmhub.io/postgresql/integrations?channel=16/stable)
* [**Timescale Community Edition**](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/how-to/enable-plugins-extensions/enable-timescaledb) replaces Timescale Apache 2
* **Improved** [**built-in roles**](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/explanation/roles) - Enhanced role-based access control system
* **New** [**refresh process**](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/how-to/refresh/index) for in-place upgrades

### Deprecated or removed

Important changes to keep in mind when migrating from 14 to 16:

* **Legacy interface `psql`** - Endpoints `db` and `db-admin` are no longer supported
  * See [](https://canonical-charmed-postgresql.readthedocs-hosted.com/16/explanation/interfaces-and-endpoints) for current supported interfaces
* **Support for Juju < `v3.6` removed**
  * Charmed PostgreSQL 16 requires Juju `3.6+ LTS` due to [Juju secrets](https://documentation.ubuntu.com/juju/3.6/reference/secret/index.html) support
* **Juju actions `get-password` and `set-password` removed**
  * Replaced by [Juju secrets](https://documentation.ubuntu.com/juju/3.6/reference/secret/index.html) for enhanced security
* **[Timescale Apache 2 edition](https://docs.timescale.com/about/latest/timescaledb-editions/) replaced**
  * Now uses [Timescale Community edition](https://docs.timescale.com/about/latest/timescaledb-editions/)
* **Charm action `set-tls-private-key` removed**
  * Will be re-introduced as Juju User Secrets in future releases
* **Charm actions renamed for consistency:**
  * `pre-upgrade-check` → `pre-refresh-check`
  * `resume-upgrade` → `resume-refresh`
  * Changes align with `juju refresh` terminology
* **Charm endpoint `certificates` split into separate endpoints:**
  * `client-certificates` - For client certificate management
  * `peer-certificates` - For peer-to-peer certificate management

For detailed information about PostgreSQL 16 features, see the {doc}`PostgreSQL 16 releases page <postgresql-16:reference/releases>`

## PostgreSQL 14

**Maintenance mode** with bug fixes and security updates only.

* **Base:** Ubuntu 22.04 LTS (Jammy)
* **Supported architectures:** `amd64`, `arm64`
* **Channel:** `14/stable`
* **Juju version:** Partially compatible with older Juju versions down to 2.9
* **Support status:** 🔧 Bug fixes and security updates only

### Features

* [**Deployment on multiple cloud services**](/how-to/deploy/index), including Sunbeam, MAAS, AWS, GCE, and Azure
* [**Juju storage**](/how-to/deploy/juju-storage) - Flexible storage configuration options
* [**Back up and restore**](/how-to/back-up-and-restore/index), including point-in-time recovery
* [**COS integration**](/how-to/monitoring-cos/index) - Enable observability tools like Grafana, Loki, Tempo, and Parca
* [**TLS integration**](/how-to/enable-tls)
* [**LDAP integration**](/how-to/enable-ldap) - Centralised authentication for PostgreSQL clusters 
* [**`amd64` and `arm64`architecture** support](/reference/system-requirements)

For detailed information about all PostgreSQL 14 releases, see the [Releases page](/reference/releases).

## Choosing a version

| Version | Support Status | Base | Juju Version | Key Features |
|---------|----------------|------|--------------|-------------|
| **PostgreSQL 16** | ![check] Active development | Ubuntu 24.04 LTS | 3.6+ LTS | Modern features, enhanced security, Juju Spaces |
| **PostgreSQL 14** | 🔧 Maintenance mode | Ubuntu 22.04 LTS | 2.9+ | Core database features, stable platform |
| **Legacy** | ![cross] Deprecated | Older base | Legacy versions | Not recommended |


* **For new deployments**: Use **PostgreSQL 16** for the latest features and long-term support
* **For existing PostgreSQL 14 deployments**: Continue using PostgreSQL 14 or plan migration to 16
* **For legacy charm users**: Migrate to PostgreSQL 14 as soon as possible

<!-- Links -->

[PostgreSQL 16]: https://charmhub.io/postgresql?channel=16/beta
[PostgreSQL 14]: https://charmhub.io/postgresql?channel=14/stable
[Legacy PostgreSQL charm]: https://charmhub.io/postgresql?channel=latest/stable

[Version 16 Release Notes]: /reference/version-16-release-notes
[release notes]: /reference/releases
[legacy charm explanation page]: /explanation/charm-versions/legacy-charm

[cross]: https://img.icons8.com/?size=16&id=CKkTANal1fTY&format=png&color=D00303
[check]: https://img.icons8.com/color/20/checkmark--v1.png