# Charmed PostgreSQL Operator

## Description

The Charmed PostgreSQL Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on machine clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps Postgres versions shipped by the Ubuntu focal series, providing lifecycle management and handling events (install, configure, integrate, remove, etc).

## Usage

Bootstrap a [lxd controller](https://juju.is/docs/olm/lxd#heading--create-a-controller) to juju and create a model:

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
juju run-action postgresql/<unit_number> get-primary --wait
```

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



## Relations

Supported [relations](https://juju.is/docs/olm/relations):

#### New `postgresql_client` interface:

Relations to new applications are supported via the `postgresql_client` interface. To create a relation: 

```shell
juju relate postgresql application
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

## Security
Security issues in the Charmed PostgreSQL Operator can be reported through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File). Please do not file GitHub issues about security issues.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md) for developer guidance.

## License
The Charmed PostgreSQL Operator is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](https://github.com/canonical/postgresql-operator/blob/main/LICENSE) for more information.
