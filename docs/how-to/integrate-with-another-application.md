# How to integrate with another application

[Integrations](https://juju.is/docs/juju/relation), also known as “relations” are connections between two applications with compatible endpoints. These connections simplify the creation and management of users, passwords, and other shared data.

This guide shows how to integrate Charmed PostgreSQL with both charmed and non-charmed applications.

For developer information about how to integrate your own charmed application with PostgreSQL, see [Development > How to integrate with your charm](/how-to/integrate-with-your-charm).

## Integrate with a charmed application

Integrations with charmed applications are supported via the modern [`postgresql_client`](https://github.com/canonical/charm-relation-interfaces/blob/main/interfaces/postgresql_client/v0/README.md) interface, and the legacy `psql` interface from the [original version](https://launchpad.net/postgresql-charm) of the charm.

```{note}
You can see which existing charms are compatible with PostgreSQL in the [Integrations](https://charmhub.io/postgresql/integrations) tab.
```

To integrate with a charmed application that supports the `postgresql_client` interface, run

```shell
juju integrate postgresql:database <charm>
```

To remove the integration, run

```shell
juju remove-relation postgresql <charm>
```

## Integrate with a non-charmed application

To integrate with an application outside of Juju, you must use the [`data-integrator` charm](https://charmhub.io/data-integrator) to create the required credentials and endpoints.

Deploy `data-integrator`:

```shell
juju deploy data-integrator --config database-name=<name>
```

Integrate with PostgreSQL:

```shell
juju integrate data-integrator postgresql
```

Use the `get-credentials` action to retrieve credentials from `data-integrator`:

```shell
juju run data-integrator/leader get-credentials
```

## Rotate application passwords

To rotate the passwords of users created for integrated applications, the integration should be removed and integrated again. This process will generate a new user and password for the application.

```shell
juju remove-relation <charm> postgresql
juju integrate <charm> postgresql
```

`<charm>` can be `data-integrator` in the case of connecting with a non-charmed application.

### Internal operator user

The `operator` user is used internally by the Charmed PostgreSQL application. All user credentials are managed with Juju secrets.

```{seealso}
* {ref}`manage-passwords`
* [Juju | How to update a secret](https://documentation.ubuntu.com/juju/latest/howto/manage-secrets/#update-a-secret)
```

## Request a custom username

Charms can request a custom username to be used in their relation with PostgreSQL 16.

The simplest way to test it is to use `requested-entities-secret` field via the [`data-integrator` charm](https://charmhub.io/data-integrator).

````{dropdown} Example

```shell
$ juju deploy postgresql --channel 16/stable

$ juju add-secret myusername mylogin=mypassword
secret:d5l3do605d8c4b1gn9a0

$ juju deploy data-integrator --channel latest/edge --config database-name=mydbname --config requested-entities-secret=d5l3do605d8c4b1gn9a0
Deployed "diedge" from charm-hub charm "data-integrator", revision 307 in channel latest/edge on ubuntu@24.04/stable

$ juju grant-secret d5l3do605d8c4b1gn9a0 data-integrator

$ juju relate postgresql data-integrator

$ juju run data-integrator/leader get-credentials
...
postgresql:
  database: mydbname
  username: mylogin
  password: mypassword
  uris: postgresql://mylogin:mypassword@10.218.34.199:5432/mydbname
  version: "16.11"
  ...

$ psql postgresql://mylogin:mypassword@10.218.34.199:5432/mydbname -c "SELECT SESSION_USER, CURRENT_USER"
 session_user |       current_user        
--------------+---------------------------
 mylogin      | charmed_mydbname_owner
(1 row)
```
````

For more technical details, see the [description of the `postgresql_client` interface](https://github.com/canonical/charm-relation-interfaces/tree/main/interfaces/postgresql_client/v0)
