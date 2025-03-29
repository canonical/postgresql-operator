# How to deploy

This page aims to provide an introduction to the PostgreSQL deployment process and lists all the related guides. It contains the following sections:
* [General deployment instructions](#general-deployment-instructions)
* [Clouds](#clouds)
* [Special deployments](#special-deployments)

---

## General deployment instructions

The basic requirements for deploying a charm are the [**Juju client**](https://juju.is/docs/juju) and a machine [**cloud**](https://juju.is/docs/juju/cloud).

First, [bootstrap](https://juju.is/docs/juju/juju-bootstrap) the cloud controller and create a [model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/): 
```shell
juju bootstrap <cloud name> <controller name>
juju add-model <model name>
```

Then, either continue with the `juju` client **or** use the `terraform juju` client to deploy the PostgreSQL charm.

To deploy with the `juju` client:
```shell
juju deploy postgresql -n <number_of_replicas>
```
> See also: [`juju deploy` command](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/deploy/)

To deploy with `terraform juju`, follow the guide [How to deploy using Terraform].
> See also: [Terraform Provider for Juju documentation](https://canonical-terraform-provider-juju.readthedocs-hosted.com/en/latest/)

If you are not sure where to start or would like a more guided walkthrough for setting up your environment, see the [Charmed PostgreSQL tutorial][Tutorial].

## Clouds

The guides below go through the steps to install different cloud services and bootstrap them to Juju:
* [Sunbeam]
* [Canonical MAAS]
* [Amazon Web Services EC2]
* [Google Cloud Engine]
* [Azure]

[How to deploy on multiple availability zones (AZ)] demonstrates how to deploy a cluster on a cloud using different AZs for high availability.

## Special deployments

These guides cover some specific deployment scenarios and architectures.

### External TLS access 
[How to deploy for external TLS VIP access] goes over an example deployment of PostgreSQL, PgBouncer and HAcluster that require external TLS/SSL access via [Virtual IP (VIP)](https://en.wikipedia.org/wiki/Virtual_IP_address).

See also:
* [How to enable TLS]
* [How to connect from outside the local network]

### Airgapped
[How to deploy in an offline or air-gapped environment] goes over the special configuration steps for installing PostgreSQL in an airgapped environment via CharmHub and the Snap Store Proxy.

### Cluster-cluster replication
Cluster-cluster, cross-regional, or multi-server asynchronous replication focuses on disaster recovery by distributing data across different servers. 

The [Cross-regional async replication] guide goes through the steps to set up clusters for cluster-cluster replication, integrate with a client, and remove or recover a failed cluster.

---

<!--Links-->

[Tutorial]: /t/9707

[How to deploy using Terraform]: /t/14916

[Sunbeam]: /t/15972
[Canonical MAAS]: /t/14293
[Amazon Web Services EC2]: /t/15703
[Google Cloud Engine]: /t/15722
[Azure]: /t/15733
[How to deploy on multiple availability zones (AZ)]: /t/15749

[How to deploy for external TLS VIP access]: /t/16576
[How to enable TLS]: /t/9685
[How to connect from outside the local network]: /t/15802

[How to deploy in an offline or air-gapped environment]: /t/15746
[Cross-regional async replication]: /t/15412