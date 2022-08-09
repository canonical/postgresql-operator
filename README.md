# PostgreSQL Operator

## Description

The PostgreSQL Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on machine clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps Postgres versions shipped by the Ubuntu focal series, providing lifecycle management and handling events (install, configure, integrate, remove, etc).

## Usage

To deploy this charm using Juju 2.9.0 or later, run:

```shell
juju add-model postgresql
charmcraft pack
juju deploy ./postgresql_ubuntu-20.04-amd64.charm
```

Note: the above model must exist outside of a k8s environment (you could bootstrap an lxd environment).

To confirm the deployment, you can run:

```shell
juju status
```

Once PostgreSQL starts up, it will be running on the default port (5432).

If required, you can remove the deployment completely by running:

```shell
juju destroy-model -y postgresql --destroy-storage
```

Note: the `--destroy-storage` will delete any data persisted by PostgreSQL.

## Relations

This charm implements the [provides data platform library](https://charmhub.io/data-platform-libs/libraries/database_provides), with the `mysql_client` interface.
To relate to it, use the [requires data-platform library](https://charmhub.io/data-platform-libs/libraries/database_requires).

Adding a relation is accomplished with:

```shell
juju relate mycharm:database postgresql:database
```

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md) for developer guidance.
