# Scale your replicas

In this section, you will learn to scale your Charmed PostgreSQL by adding or removing juju units. 

The Charmed PostgreSQL VM operator uses a [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, and automatic failover. The charm uses PostgreSQL’s [synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-k8s-synchronous-replication) with Patroni.

```{caution}
This tutorial hosts all replicas on the same machine. 

**This should not be done in a production environment.** 

To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).
```

## Add units

Currently, your deployment has only one juju **unit**, known in juju as the **leader unit**. You can think of this as the database **primary instance**. For each **replica**, a new unit is created. All units are members of the same database cluster.

To add two replicas to your deployed PostgreSQL application, run
```text
juju add-unit postgresql -n 2
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. 

You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
```text
TODO
```

## Remove units

Removing a unit from the application scales down the replicas.

Before we scale them down, list all the units with `juju status`. You will see three units: `postgresql/0`, `postgresql/1`, and `postgresql/2`. Each of these units hosts a PostgreSQL replica. 

To remove the replica hosted on the unit `postgresql/2` enter:
```text
juju remove-unit postgresql/2
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:

```text
TODO
```

