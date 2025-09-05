# Modern PostgreSQL charm

Modern PostgreSQL charms are [Ops charms] released in the Charmhub channels `14/` (PostgreSQL 14) and `16/` (PostgreSQL 16). 

They provide the `database` endpoint for the `postgresql_client` interface.

PostgreSQL 14 (track `14/`) provides `db` and `db-admin` endpoints for the legacy `pgsql` interface, and supports migration from the legacy charm.

PostgreSQL 16 (track `16/`) does not provide any legacy interfaces and does not support migration.

## PostgreSQL 16

**Latest stable version** with active feature development.

* **Base:** Ubuntu 24.04 LTS (Noble)
* **Supported architectures:** `amd64`, `arm64`
* **Channel:** `16/stable` (latest development available for testing in `16/edge`)
* **Juju version:** Requires Juju 3.6+ LTS
* **Support status:** ![check] Active development and full support

PostgreSQL 16 includes modern features like Juju Spaces support, enhanced security, extended monitoring capabilities, and improved high availability features.

For detailed information about new features, improvements, and breaking changes, see [PostgreSQL 16 release notes](https://github.com/canonical/postgresql-operator/releases).

## PostgreSQL 14

**Maintenance mode** with bug fixes and security updates only.

* **Base:** Ubuntu 22.04 LTS (Jammy)
* **Supported architectures:** `amd64`, `arm64`
* **Channel:** `14/stable`
* **Juju version:** Partially compatible with older Juju versions down to 2.9
* **Support status:** 🔧 Bug fixes and security updates only

PostgreSQL 14 provides essential database features including deployment flexibility, backup and restore capabilities, monitoring integration, TLS support, and multi-architecture compatibility.

For detailed information about PostgreSQL 14 features, see the [PostgreSQL 14 releases page](https://canonical-charmed-postgresql.readthedocs-hosted.com/14/reference/releases/)

## Choosing a version

| Version | Support Status | Base | Juju Version | Key Features |
|---------|----------------|------|--------------|-------------|
| **PostgreSQL 16** | ![check] Active development | Ubuntu 24.04 LTS | 3.6+ LTS | Modern features, enhanced security, Juju Spaces |
| **PostgreSQL 14** | 🔧 Maintenance mode | Ubuntu 22.04 LTS | Compatible with older versions | Core database features, stable platform |
| **Legacy** | ![cross] Deprecated | Older base | Legacy versions | Not recommended |


* **For new deployments**: Use **PostgreSQL 16** for the latest features and long-term support
* **For existing PostgreSQL 14 deployments**: Continue using PostgreSQL 14 or plan migration to 16
* **For legacy charm users**: Migrate to PostgreSQL 14 as soon as possible

## Configuration options

The legacy charm config options were not moved to the modern charms. Modern charms apply the best possible configuration automatically. 

Feel free to [contact us](/reference/contacts) about the database tuning and configuration options.

## Extensions supported by modern charm

The legacy charm provided plugins/extensions enabling through the relation (interface `pgsql`).This is NOT supported by modern charms (neither `pgsql` nor `postgresql_client` interfaces). Please enable the necessary extensions using appropriate `plugin_*_enable` [config option](https://charmhub.io/postgresql/configure) of the modern charm. After enabling the modern charm, it will provide plugins support for both `pgsql` (only if it's PostgreSQL 14) and `postgresql_client` interfaces.

See: [](/reference/plugins-extensions)

Feel free to [contact us](/reference/contacts) if there is a particular extension you are interested in.

## Roles supported by modern charm

In the legacy charm, the user could request roles by setting the `roles` field to a comma separated list of desired roles. This is NOT supported by the `14/` modern charm implementation of the legacy `pgsql` interface. 

The same functionality is provided via the modern `postgresql_client` using [extra-user-roles](/explanation/users). 

For more information about migrating the new interface on PostgreSQL 14, see [How to integrate PostgreSQL with your charm](https://canonical-charmed-postgresql.readthedocs-hosted.com/14/how-to/development/integrate-with-your-charm/).

## Workload artifacts

The legacy charm used to deploy PostgreSQL from APT/Debian packages,
while the modern charm installs and operates PostgreSQL snap [charmed-postgresql](https://snapcraft.io/charmed-postgresql). 

See: [](explanation/architecture).

<!-- Links -->

[PostgreSQL 16]: https://charmhub.io/postgresql?channel=16/beta
[PostgreSQL 14]: https://charmhub.io/postgresql?channel=14/stable
[Legacy PostgreSQL charm]: https://charmhub.io/postgresql?channel=latest/stable

[Version 16 Release Notes]: /reference/version-16-release-notes
[release notes]: /reference/releases
[legacy charm explanation page]: /explanation/legacy-charm

[cross]: https://img.icons8.com/?size=16&id=CKkTANal1fTY&format=png&color=D00303
[check]: https://img.icons8.com/color/20/checkmark--v1.png