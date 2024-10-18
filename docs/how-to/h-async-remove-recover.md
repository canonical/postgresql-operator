# Remove or recover a cluster

This guide will cover how to manage clusters using an example PostgreSQL deployment with two servers: one in Rome and one in Lisbon.

## Prerequisites
* Juju `v.3.4.2+`
* Make sure your machine(s) fulfill the [system requirements](/t/11743)
* See [supported target/source model relationships](/t/15412#substrate-dependencies).
* A cross-regional async replication setup
  * See [How to set up clusters](/t/13991)

## Summary
* [Switchover](#switchover)
* [Detach a cluster](#detach-a-cluster)
  * [Reuse a detached cluster](#reuse-a-detached-cluster)
  * [Remove a detached cluster](#remove-a-detached-cluster)
* [Recover a cluster](#recover-a-cluster)

<!-- TODO: Rethink sections, especially "recover" -->
---

## Switchover

If the primary cluster fails or is removed, it is necessary to appoint a new cluster as primary.

To switchover and promote `lisbon` to primary, one would run the command:

```shell
juju run -m lisbon db2/leader promote-to-primary
```

## Detach a cluster

Clusters in an async replica set can be detached. The detached cluster can then be either removed or reused.

Assuming `lisbon` is the current primary, one would run the following command to detach `rome`:

```shell
juju remove-relation -m lisbon replication-offer db2:replication
```

The command above will move the `rome` cluster into a detached state (`blocked`) keeping all the data in place.

### Reuse a detached cluster

The following command creates a new cluster in the replica set from the detached `rome` cluster, keeping its existing data in use:

```shell
juju run -m rome db1/leader promote-to-primary
```
### Remove a detached cluster

The following command removes the detached `rome` cluster and **destroys its stored data** with the optional `--destroy-storage` flag:

```shell
juju remove-application -m rome db1 --destroy-storage
```

## Recover a cluster

**If the integration between clusters was removed** and one side went into a  `blocked` state, integrate both clusters again and call the `promote-cluster` action to restore async replication - similar to the "Reuse a detached cluster" step above.

**If the cluster group lost a member entirely** (e.g. `rome` is suddenly no longer available to the cluster group originally consisting of `rome` and `lisbon`), deploy a new `postgresql` application and [set up async replication](/t/13991). The data will be copied automatically after the `promote-cluster` action is called, and the new cluster will join the cluster group.