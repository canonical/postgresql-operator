# Integrate with a client application

This guide will show you how to integrate a client application with a cross-regional async setup using an example PostgreSQL deployment with two servers: one in Rome and one in Lisbon.

## Prerequisites
* Juju `v.3.4.2+`
* Make sure your machine(s) fulfill the [system requirements](/t/11743)
* See [supported target/source model relationships](/t/15412#substrate-dependencies).
* A cross-regional async replication setup
  * See [How to set up clusters](/t/13991)

## Summary
* [Configure database endpoints](#configure-database-endpoints)
* [Internal client](#internal-client)
* [External client](#external-client)

---

## Configure database endpoints

To make your database available to a client application, you must first offer and consume database endpoints.

### Offer database endpoints

[Offer](https://juju.is/docs/juju/offer) the `database` endpoint on each of the `postgresql` applications.

```shell
juju switch rome
juju offer db1:database db1database

juju switch lisbon
juju offer db2:database db2database
```

### Consume endpoints on client app

It is good practice to use a separate model for the client application rather than using one of the database host models.
 
```shell
juju add-model app
juju switch app
juju consume rome.db1database
juju consume lisbon.db2database
```

## Internal client

If the client application is another charm, deploy them and connect them with `juju integrate`.

<!--TODO: Clarify code--->

```shell
juju switch app

juju deploy postgresql-test-app
juju deploy pgbouncer --channel 1/stable

juju integrate postgresql-test-app:database pgbouncer
juju integrate pgbouncer db1database
```

## External client

If the client application is external, they must be integrated via the [`data-integrator` charm](https://charmhub.io/data-integrator).

<!--TODO: Clarify code--->

```shell
juju switch app

juju deploy data-integrator --config database-name=mydatabase
juju deploy pgbouncer pgbouncer-external --channel 1/stable

juju relate data-integrator pgbouncer-external
juju relate pgbouncer-external db1database

juju run data-integrator/leader get-credentials
```