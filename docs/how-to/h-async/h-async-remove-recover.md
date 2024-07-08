# Remove or recover a cluster
[note type="caution"]
This is an internal article. **Do not use it in production!** 

Contact the [Canonical Data Platform team](https://chat.charmhub.io/charmhub/channels/data-platform) if you are interested in this topic.
[/note]

This guide will cover how to manage clusters using an example PostgreSQL deployment with two servers: one in Rome and one in Lisbon.

## Prerequisites
* `juju v.3.4.2+`
* A cross-regional async replication setup
  * Refer to the page [How to set up clusters](/t/13991)

## Summary
* [Switchover](#heading--switchover)
* [Detach a cluster](#heading--detach)
  * [Reuse a detached cluster](#heading--reuse)
  * [Remove a detached cluster](#heading--remove)
* [Recover a cluster](#heading--recover)

<!-- TODO: Rethink sections, especially "recover" -->
---

<a href="#heading--switchover"><h2 id="heading--switchover"> Switchover </h2></a>

If the primary cluster fails or is removed, it is necessary to appoint a new cluster as primary.

To switchover and promote `lisbon` to primary, one would run the command:

```shell
juju run -m lisbon db2/leader promote-to-primary
```

<a href="#heading--detach"><h2 id="heading--detach"> Detach a cluster </h2></a>

Clusters in an async replica set can be detached. The detached cluster can then be either removed or reused.

Assuming `lisbon` is the current primary, one would run the following command to detach `rome`:

```shell
juju remove-relation -m lisbon replication-offer db2:replication
```

The command above will move the `rome` cluster into a detached state (`blocked`) keeping all the data in place.

<a href="#heading--reuse"><h3 id="heading--reuse"> Reuse a detached cluster </h3></a>

The following command creates a new cluster in the replica set from the detached `rome` cluster, keeping its existing data in use:

```shell
juju run -m rome db1/leader promote-to-primary
```
<a href="#heading--remove"><h3 id="heading--remove"> Remove a detached cluster </h3></a>

The following command removes the detached `rome` cluster and **destroys its stored data** with the optional `--destroy-storage` flag:

```shell
juju remove-application -m rome db1 --destroy-storage
```

<a href="#heading--recover"><h2 id="heading--recover"> Recover a cluster </h2></a>

**If the integration between clusters was removed** and one side went into a  `blocked` state, integrate both clusters again and call the `promote-cluster` action to restore async replication - similar to the "Reuse a detached cluster" step above.

**If the cluster group lost a member entirely** (e.g. `rome` is suddenly no longer available to the cluster group originally consisting of `rome` and `lisbon`), deploy a new `postgresql` application and [set up async replication](/t/13991). The data will be copied automatically after the `promote-cluster` action is called, and the new cluster will join the cluster group.