# PostgreSQL Operator

## Description

The PostgreSQL Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on machine clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps Postgres versions shipped by the Ubuntu bionic and focal series, providing lifecycle management and handling events (install, configure, integrate, remove).

## Usage

As this charm is not yet published, you need to follow the build and deploy instructions from [CONTRIBUTING.md](CONTRIBUTING.md).

## Accessing the database

You can access the database using any PostgreSQL client by connecting on the unit address and port `5432` as user `postgres` with the password shown by the command below.

```bash
juju run-action postgresql/0 get-initial-password --wait
```