# Legacy PostgreSQL charm

The legacy PostgreSQL charm is a [Reactive charm](https://documentation.ubuntu.com/juju/3.6/reference/charm/#reactive-charm) in the Charmhub channel `latest/stable`. 

It provided `db` and `db-admin` endpoints for the `pgsql` interface.

**We strongly advise against using the now deprecated `latest/` track**. It will be removed from Charmhub in the near future.

For more information about the modern charms and their differences to the legacy charm, see [](/explanation/charm-versions/modern-charm).

## The default Charmhub track

The [default track](https://docs.openstack.org/charm-guide/yoga/project/charm-delivery.html) was switched from the `latest/` to `14/` to ensure all new deployments use a modern codebase. See [this Discourse post](https://discourse.charmhub.io/t/request-switch-default-track-from-latest-to-14-for-postgresql-k8s-charms/10314) for more information about the switch.

## How to migrate from legacy to modern

It is not possible to quickly migrate from the legacy charm to the PostgreSQL 16 charm - this was only possible with {doc}`PostgreSQL 14 <postgresql-14:explanation/legacy-charm>`.

To migrate from the legacy PostgreSQL charm to the modern PostgreSQL 16 charm, you must implement the modern `database` endpoint for the `postgresql_client` interface on your charm.  

See: [](/how-to/development/integrate-with-your-charm)

## How to deploy the legacy PostgreSQL charm

Deploy the charm using the channel `latest/stable`:

```yaml
  postgresql:
    charm: postgresql
    channel: latest/stable
```

```{caution}
Remove the charm store prefix `cs:` from the bundle. Otherwise, the modern charm will be chosen by Juju (due to the default track pointing to `14/stable` and not `latest/stable`).

A common error message is: `cannot deploy application "postgresql": unknown option "..."`.
```

## How to report issues and contact authors

The legacy charm (from `latest/stable`) is stored on [Launchpad](https://git.launchpad.net/postgresql-charm/). Report legacy charm issues [here](https://bugs.launchpad.net/postgresql-charm).

The modern charms are stored on GitHub: [PostgreSQL 14 branch](https://github.com/canonical/postgresql-operator/tree/main) and [PostgreSQL 16 branch](https://github.com/canonical/postgresql-operator/tree/16/edge) . Report modern charm issues [here](https://github.com/canonical/postgresql-operator/issues/new/choose).

Do you have questions? [Contact us](/reference/contacts)!

