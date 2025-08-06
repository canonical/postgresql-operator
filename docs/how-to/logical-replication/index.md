# Logical replication

Logical replication is a feature that allows replicating a subset of one PostgreSQL cluster data to another PostgreSQL cluster.

Under the hood, it uses the publication and subscriptions mechanisms from the [PostgreSQL logical replication](https://www.postgresql.org/docs/16/logical-replication.html) feature.

## Prerequisites
* Juju `v.3.6.8+`
* Make sure your machine(s) fulfil the [](/reference/system-requirements)
* Have two clusters running PostgreSQL and managed by this machine operator (for the guides, let's consider they are named `postgresql1` and `postgresql2`).

## Guides

* [](/how-to/logical-replication/set-up-clusters)
* [](/how-to/logical-replication/re-enable)
