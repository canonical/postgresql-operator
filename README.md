# Charmed PostgreSQL VM Operator
[![CharmHub Badge](https://charmhub.io/postgresql/badge.svg)](https://charmhub.io/postgresql)
[![Release](https://github.com/canonical/postgresql-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/postgresql-operator/actions/workflows/release.yaml)
[![Tests](https://github.com/canonical/postgresql-operator/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/canonical/postgresql-operator/actions/workflows/ci.yaml?query=branch%3Amain)

## Description

This repository contains a [Juju Charm](https://charmhub.io/postgresql) for deploying [PostgreSQL](https://www.postgresql.org/about/) on virtual machines ([LXD](https://ubuntu.com/lxd)).
To deploy on Kubernetes, please use [Charmed PostgreSQL K8s Operator](https://charmhub.io/postgresql-k8s).

This operator provides a PostgreSQL database with replication enabled: one primary instance and one (or more) hot standby replicas. The Operator in this repository is a Python script which wraps PostgreSQL versions distributed by Ubuntu Jammy series and adding [Patroni](https://github.com/zalando/patroni) on top of it, providing lifecycle management and handling events (install, configure, integrate, remove, etc).

## Usage

Bootstrap a [lxd controller](https://juju.is/docs/olm/lxd#heading--create-a-controller) and create a new Juju model:

```shell
juju add-model postgresql
```

### Basic Usage
To deploy a single unit of PostgreSQL using its default configuration.

```shell
juju deploy postgresql --channel edge
```

It is customary to use PostgreSQL with replication. Hence usually more than one unit (preferably an odd number to prohibit a "split-brain" scenario) is deployed. To deploy PostgreSQL with multiple replicas, specify the number of desired units with the `-n` option.

```shell
juju deploy postgresql --channel edge -n <number_of_units>
```

To retrieve primary replica one can use the action `get-primary` on any of the units running PostgreSQL.
```shell
juju run-action postgresql/leader get-primary --wait
```

Similarly, the primary replica is displayed as a status message in `juju status`, however one
should note that this hook gets called on regular time intervals and the primary may be outdated if
the status hook has not been called recently.

### Replication
#### Adding Replicas

To add more replicas one can use the `juju add-unit` functionality i.e.
```shell
juju add-unit postgresql -n <number_of_units_to_add>
```
The implementation of `add-unit` allows the operator to add more than one unit, but functions internally by adding one replica at a time, avoiding multiple replicas syncing from the primary at the same time.

#### Removing Replicas

Similarly to scale down the number of replicas the `juju remove-unit` functionality may be used i.e.
```shell
juju remove-unit postgresql <name_of_unit1> <name_of_unit2>
```
The implementation of `remove-unit` allows the operator to remove more than one unit. The functionality of `remove-unit` functions by removing one replica at a time to avoid downtime.

### Password rotation

#### Charm users

For users used internally by the Charmed PostgreSQL Operator an action can be used to rotate their passwords.
```shell
juju run-action postgresql/leader set-password username=<username> password=<password> --wait
```
Note: currently, the users used by the operator are `operator`, `replication`, `backup` and `rewind`. Those users should not be used outside the operator.

#### Related applications users

To rotate the passwords of users created for related applications the relation should be removed and the application should be related again to the Charmed PostgreSQL Operator. That process will generate a new user and password for the application (removing the old user).

## Integrations (Relations)

Supported [relations](https://juju.is/docs/olm/relations):

#### New `postgresql_client` interface:

Current charm relies on [Data Platform libraries](https://charmhub.io/data-platform-libs). Your
application should define an interface in `metadata.yaml`:

```yaml
requires:
  database:
    interface: postgresql_client
```

Please read usage documentation about
[data_interfaces](https://charmhub.io/data-platform-libs/libraries/data_interfaces) library for
more information about how to enable PostgreSQL interface in your application.

Relations to new applications are supported via the `postgresql_client` interface. To create a
relation:

juju v2.x:

```shell
juju relate postgresql application
```

juju v3.x

```shell
juju integrate postgresql application
```

To remove a relation:
```shell
juju remove-relation postgresql application
```

#### Legacy `pgsql` interface:
We have also added support for the two database legacy relations from the [original version](https://launchpad.net/postgresql-charm) of the charm via the `pgsql` interface. Please note that these relations will be deprecated.
 ```shell
juju relate postgresql:db mailman3-core
juju relate postgresql:db-admin landscape-server
```

#### `tls-certificates` interface:

The Charmed PostgreSQL Operator also supports TLS encryption on internal and external connections. To enable TLS:

```shell
# Deploy the TLS Certificates Operator. 
juju deploy tls-certificates-operator --channel=edge
# Add the necessary configurations for TLS.
juju config tls-certificates-operator generate-self-signed-certificates="true" ca-common-name="Test CA" 
# Enable TLS via relation.
juju relate postgresql tls-certificates-operator
# Disable TLS by removing relation.
juju remove-relation postgresql tls-certificates-operator
```

Note: The TLS settings shown here are for self-signed-certificates, which are not recommended for production clusters. The TLS Certificates Operator offers a variety of configurations. Read more on the TLS Certificates Operator [here](https://charmhub.io/tls-certificates-operator).

## Security
Security issues in the Charmed PostgreSQL Operator can be reported through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File). Please do not file GitHub issues about security issues.

## Contributing
Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md) for developer guidance.

## License
The Charmed PostgreSQL Operator [is distributed](https://github.com/canonical/postgresql-operator/blob/main/LICENSE) under the Apache Software License, version 2.0. It installs/operates/depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which [is licensed](https://www.postgresql.org/about/licence/) under PostgreSQL License, a liberal Open Source license, similar to the BSD or MIT licenses.

## Trademark Notice
PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are property of their respective owners.
