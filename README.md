# Charmed PostgreSQL VM Operator
[![CharmHub Badge](https://charmhub.io/postgresql/badge.svg)](https://charmhub.io/postgresql)
[![Release](https://github.com/canonical/postgresql-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/postgresql-operator/actions/workflows/release.yaml)
[![Tests](https://github.com/canonical/postgresql-operator/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/canonical/postgresql-operator/actions/workflows/ci.yaml?query=branch%3Amain)
[![codecov](https://codecov.io/gh/canonical/postgresql-operator/graph/badge.svg?token=4V2mu7aWmu)](https://codecov.io/gh/canonical/postgresql-operator)


This repository contains a charmed operator for deploying [PostgreSQL](https://www.postgresql.org/about/) on virtual machines via the [Juju orchestration engine](https://juju.is/).

To learn more about how to deploy and operate Charmed PostgreSQL, see the [official documentation](https://canonical-charmed-postgresql.readthedocs-hosted.com/).

## Overview

This operator provides a PostgreSQL database with replication enabled: one primary instance and one (or more) hot standby replicas. The Operator in this repository is a Python script which wraps PostgreSQL versions distributed by Ubuntu Jammy series and adding [Patroni](https://github.com/zalando/patroni) on top of it, providing lifecycle management and handling events (install, configure, integrate, remove, etc).
  
## Basic usage

### Deployment

Bootstrap a [lxd controller](https://juju.is/docs/olm/lxd#heading--create-a-controller) and create a new Juju model:

```shell
juju add-model sample-model
```

To deploy a single unit of PostgreSQL using its [default configuration](config.yaml), run the following command:

```shell
juju deploy postgresql --channel 14/stable
```

It is customary to use PostgreSQL with replication to ensure high availability. A replica is equivalent to a juju unit.

To deploy PostgreSQL with multiple replicas, specify the number of desired units with the `-n` option:

```shell
juju deploy postgresql --channel 14/stable -n <number_of_units>
```

To add replicas to an existing deployment, see the [Add replicas](#add-replicas) section.

>[!TIP]
>It is generally recommended to have an odd number of units to avoid a "[split-brain](https://en.wikipedia.org/wiki/Split-brain_(computing))" scenario

### Primary replica

To retrieve the primary replica, use the action `get-primary` on any of the units running PostgreSQL.

```shell
juju run postgresql/leader get-primary
```

Similarly, the primary replica is displayed as a status message in `juju status`. Note that this hook gets called at regular time intervals, so the primary may be outdated if the status hook has not been called recently.

### Replication

#### Add replicas

To add more replicas one can use the `juju add-unit` functionality i.e.

```shell
juju add-unit postgresql -n <number_of_units_to_add>
```

The implementation of `add-unit` allows the operator to add more than one unit, but functions internally by adding one replica at a time. This is done to avoid multiple replicas syncing from the primary at the same time.

#### Remove replicas

To scale down the number of replicas the `juju remove-unit` functionality may be used i.e.

```shell
juju remove-unit postgresql <name_of_unit_1> <name_of_unit_2>
```

The implementation of `remove-unit` allows the operator to remove more than one unit. The functionality of `remove-unit` functions by removing one replica at a time to avoid downtime.

### Password rotation

#### Charm users

To rotate the password of users internal to the Charmed PostgreSQL operator, use the `set-password` action as follows:

```shell
juju run postgresql/leader set-password username=<user> password=<password>
```

>[!NOTE]
>Currently, internal users are `operator`, `replication`, `backup` and `rewind`. These users should not be used outside the operator.

#### Integrated (related) application users

To rotate the passwords of users created for integrated applications, the integration to Charmed PostgreSQL should be removed and re-created. This process will generate a new user and password for the application (and remove the old user).

## Integrations (Relations)

Supported [integrations](https://juju.is/docs/olm/relations):

#### New `postgresql_client` interface

Current charm relies on [Data Platform libraries](https://charmhub.io/data-platform-libs). Your
application should define an interface in `metadata.yaml`:

```yaml
requires:
  database:
    interface: postgresql_client
```

Please read the usage documentation of the
[data_interfaces](https://charmhub.io/data-platform-libs/libraries/data_interfaces) library for
more information about how to enable a PostgreSQL interface in your application.

Relations to new applications are supported via the `postgresql_client` interface. To create a
relation to another application:

juju `v2.x`:

```shell
juju relate postgresql <application_name>
```

juju `v3.x`:

```shell
juju integrate postgresql <application_name>
```

To remove a relation:
```shell
juju remove-relation postgresql <application_name>
```

#### Legacy `pgsql` interface

We have also added support for the two database legacy relations from the [original version](https://launchpad.net/postgresql-charm) of the charm via the `pgsql` interface. Please note that these relations will be deprecated.
 ```shell
juju relate postgresql:db mailman3-core
juju relate postgresql:db-admin landscape-server
```

#### `tls-certificates` interface

The Charmed PostgreSQL Operator also supports TLS encryption on internal and external connections. Below is an example of enabling TLS with the [self-signed certificates charm](https://charmhub.io/self-signed-certificates).

```shell
# Deploy the self-signed certificates TLS operator. 
juju deploy self-signed-certificates --config ca-common-name="Example CA"

# Enable TLS via relation.
juju integrate postgresql self-signed-certificates

# Disable TLS by removing relation.
juju remove-relation postgresql self-signed-certificates
```

>[!WARNING]
>The TLS settings shown here are for self-signed-certificates, which are not recommended for production clusters. See the guide [Security with X.509 certificates](https://charmhub.io/topics/security-with-x-509-certificates) for an overview of available certificates charms.

## Security

Security issues in the Charmed PostgreSQL Operator can be reported through [private security reports](https://github.com/canonical/postgresql-operator/security/advisories/new) on GitHub.
For more information, see the [Security policy](SECURITY.md).

## Contributing

* For best practices on how to write and contribute to charms, see the [Juju SDK docs](https://juju.is/docs/sdk/how-to)
* For more specific developer guidance for contributions to Charmed PostgreSQL, see the file [CONTRIBUTING.md](CONTRIBUTING.md)
* Report security issues for the Charmed PostgreSQL Operator through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File).
* Report technical issues, bug reports and feature requests through the [GitHub Issues tab](https://github.com/canonical/postgresql-operator/issues).

## Licensing and trademark

The Charmed PostgreSQL Operator is distributed under the [Apache Software License, version 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE). It installs, operates and depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which is licensed under the [PostgreSQL License](https://www.postgresql.org/about/licence/), a liberal Open Source license similar to the BSD or MIT licenses.

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are property of their respective owners.
