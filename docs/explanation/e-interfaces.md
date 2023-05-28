# Interfaces/endpoints

The charm supports modern `postgresql_client` and legacy `pgsql` interfaces (in a backward compatible mode).

**Note:** do NOT relate both modern and legacy interfaces simultaneously!

## Modern interfaces

This charm provides modern ['postgresql_client' interface](https://github.com/canonical/charm-relation-interfaces). Applications can easily connect PostgreSQL using ['data_interfaces' ](https://charmhub.io/data-platform-libs/libraries/data_interfaces) library from ['data-platform-libs'](https://github.com/canonical/data-platform-libs/).

### Modern `postgresql_client` interface (`database` endpoint):

Adding a relation is accomplished with `juju relate` (or `juju integrate` for Juju 3.x) via endpoint `database`. Example:

```shell
# Deploy Charmed PostgreSQL cluster with 3 nodes
juju deploy postgresql -n 3 --channel 14

# Deploy the relevant application charms
juju deploy mycharm

# Relate PostgreSQL with your application
juju relate postgresql-k8s:database mycharm:database

# Check established relation (using postgresql_client interface):
juju status --relations

# Example of the properly established relation:
# > Relation provider      Requirer          Interface          Type
# > postgresql:database    mycharm:database  postgresql_client  regular
```

## Legacy interfaces

**Note:** Legacy relations are deprecated and will be discontinued on future releases. Usage should be avoided.

### Legacy `pgsql` interface (`db` and `db-admin` endpoints):

This charm supports legacy interface `pgsql` from the previous [PostgreSQL charm](https://launchpad.net/postgresql-charm):

```shell
juju relate postgresql:db mailman3-core
juju relate postgresql:db-admin landscape-server
```

**Note:** The endpoint `db-admin` provides the same legacy interface `pgsql` with PostgreSQL admin-level privileges. It is NOT recommended to use it from security point of view.