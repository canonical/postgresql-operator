# Integrate with a client application
[note type="caution"]
This is an internal article. **Do not use it in production!** 

Contact the [Canonical Data Platform team](https://chat.charmhub.io/charmhub/channels/data-platform) if you are interested in this topic.
[/note]

This guide will show you how to integrate a client application with a cross-regional async setup using an example PostgreSQL deployment with two servers: one in Rome and one in Lisbon.

## Prerequisites
* `juju v.3.4.2+`
* A cross-regional async replication setup
  * Refer to the page [How to set up clusters](/t/13991)

## Summary
* [Configure database endpoints](#heading--configure-endpoints)
* [Internal client](#heading--internal-client)
* [External client](#heading--external-client)

---

<a href="#heading--configure-endpoints"><h2 id="heading--configure-endpoints"> Configure database endpoints </h2></a>

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

<a href="#heading--internal-client"><h2 id="heading--internal-client"> Internal client </h2></a>

If the client application is another charm, deploy them and connect them with `juju integrate`.

<!--TODO: Clarify code--->

```shell
juju switch app

juju deploy postgresql-test-app
juju deploy pgbouncer --channel 1/stable

juju integrate postgresql-test-app:first-database pgbouncer
juju integrate pgbouncer db1database
```

<a href="#heading--external-client"><h2 id="heading--external-client"> External client </h2></a>

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