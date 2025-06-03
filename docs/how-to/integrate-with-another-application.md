---
relatedlinks: https://documentation.ubuntu.com/juju/3.6/reference/relation/, [postgresql_client&#32-&#32GitHub](https://github.com/canonical/charm-relation-interfaces/tree/main/interfaces/postgresql_client/v0)

---

# How to integrate with another application

[Integrations](https://juju.is/docs/juju/relation), also known as “relations” are connections between two applications with compatible endpoints. These connections simplify the creation and management of users, passwords, and other shared data.

This guide shows how to integrate Charmed PostgreSQL with both charmed and non-charmed applications.

For developer information about how to integrate your own charmed application with PostgreSQL, see [](/how-to/development/integrate-with-your-charm).

## Integrate with a charmed application

Integrations with charmed applications are supported via the modern [`postgresql_client`](https://github.com/canonical/charm-relation-interfaces/blob/main/interfaces/postgresql_client/v0/README.md) interface, and the legacy `psql` interface from the [original version](https://launchpad.net/postgresql-charm) of the charm.

```{note}
You can see which existing charms are compatible with PostgreSQL in the [Integrations](https://charmhub.io/postgresql/integrations) tab on Charmhub.
```

### Modern `postgresql_client` interface

To integrate with a charmed application that supports the `postgresql_client` interface, run

```text
juju integrate postgresql:database <charm>
```

To remove the integration, run

```text
juju remove-relation postgresql <charm>
```

### Legacy `pgsql` interface

```{caution}
Note that this interface is **deprecated**. See [](/explanation/legacy-charm).
```

To integrate via the legacy interface, run

 ```text
juju integrate postgresql:db <charm>
```

Extended permissions can be requested using the `db-admin` endpoint:

```text
juju integrate postgresql:db-admin <charm>
```

## Integrate with a non-charmed application

To integrate with an application outside of Juju, you must use the [`data-integrator` charm](https://charmhub.io/data-integrator) to create the required credentials and endpoints.

Deploy `data-integrator`:
```text
juju deploy data-integrator --config database-name=<name>
```

Integrate with PostgreSQL:
```text
juju integrate data-integrator postgresql
```

Use the `get-credentials` action to retrieve credentials from `data-integrator`:
```text
juju run data-integrator/leader get-credentials
```

## Rotate application passwords
To rotate the passwords of users created for integrated applications, the integration should be removed and integrated again. This process will generate a new user and password for the application.

```text
juju remove-relation <charm> postgresql
juju integrate <charm> postgresql
```

In the case of connecting with a non-charmed application, `<charm>` would be `data-integrator`.

See also: [](/how-to/manage-passwords)
