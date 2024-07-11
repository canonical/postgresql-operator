# Set up clusters for cross-regional async replication

[note type="caution"]
This is an internal article. **Do not use it in production!** 

Contact the [Canonical Data Platform team](https://chat.charmhub.io/charmhub/channels/data-platform) if you are interested in this topic.
[/note]

Cross-regional (or multi-server) asynchronous replication focuses on disaster recovery by distributing data across different servers. 

This guide will show you the basics of initiating a cross-regional async setup using an example PostgreSQL deployment with two servers: one in Rome and one in Lisbon.

## Summary
* [Deploy](#heading--deploy)
* [Offer](#heading--offer)
* [Consume](#heading--consume)
* [Promote or switchover](#heading--promote-switchover)
* [Scale](#heading--scale)


---

<a href="#heading--deploy"><h2 id="heading--deploy"> Deploy </h2></a>

To deploy two clusters in different servers, create two juju models - one for the `rome` cluster, one for the `lisbon` cluster. In the example below, we use the config flag `profile=testing` to limit memory usage.

```shell
juju add-model rome 
juju add-model lisbon

juju switch rome # active model must correspond to cluster
juju deploy postgresql db1 --channel=14/edge --config profile=testing --base ubuntu@22.04

juju switch lisbon 
juju deploy postgresql db2 --channel=14/edge --config profile=testing --base ubuntu@22.04
```

<a href="#heading--offer"><h2 id="heading--offer"> Offer </h2></a>

[Offer](https://juju.is/docs/juju/offer) asynchronous replication in one of the clusters.

```shell
juju switch rome
juju offer db1:replication-offer replication-offer
``` 

<a href="#heading--consume"><h2 id="heading--consume"> Consume </h2></a>

Consume asynchronous replication on planned `Standby` cluster (Lisbon):
```shell
juju switch lisbon
juju consume rome.replication-offer
juju integrate replication-offer db2:replication
``` 

<a href="#heading--promote-switchover"><h2 id="heading--promote-switchover"> Promote or switchover a cluster </h2></a>

To define the primary cluster, use the `create-replication` action.

```shell
juju run -m rome db1/leader create-replication
```

To switchover and use `lisbon` as the primary instead, run

```shell
juju run -m lisbon db2/leader promote-to-primary
```

<a href="#heading--scale"><h2 id="heading--scale"> Scale a cluster </h2></a>

The two clusters work independently, which means that it’s possible to scale each cluster separately. The `-m` flag defines the target of this action, so it can be performed with any active model. 

For example:

```shell
juju add-unit db1 -n 2 -m rome
juju add-unit db2 -n 2 -m lisbon
```
[note]
Scaling is possible before and after the asynchronous replication is established/created.
[/note]