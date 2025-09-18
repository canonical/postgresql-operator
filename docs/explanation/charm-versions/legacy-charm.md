# Legacy PostgreSQL charm

The legacy PostgreSQL charm is a [Reactive charm](https://documentation.ubuntu.com/juju/3.6/reference/charm/#reactive-charm) in the **now deprecated** Charmhub channel `latest/stable`. 

The PostgreSQL 16 charm does not support the same endpoints as the legacy charm. To migrate from legacy to PostgreSQL 16, you must implement the modern `database` endpoint for the `postgresql_client` interface on your charm.

To read more about implementing compatible endpoints, see: [](/how-to/development/integrate-with-your-charm)

To read more about the legacy charm see the {doc}`PostgreSQL 14 documentation <postgresql-14:explanation/legacy-charm>`.

## How to report issues and contact authors

The legacy charm (from `latest/stable`) is stored on [Launchpad](https://git.launchpad.net/postgresql-charm/). Report legacy charm issues [here](https://bugs.launchpad.net/postgresql-charm).

The modern charms are stored on GitHub: [PostgreSQL 14 branch](https://github.com/canonical/postgresql-operator/tree/main) and [PostgreSQL 16 branch](https://github.com/canonical/postgresql-operator/tree/16/edge) . Report modern charm issues [here](https://github.com/canonical/postgresql-operator/issues/new/choose).

Do you have questions? [Contact us](/reference/contacts)!

