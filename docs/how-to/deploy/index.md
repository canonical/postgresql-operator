# How to deploy

This page aims to provide an introduction to the PostgreSQL deployment process and lists all the related guides. It contains the following sections:
- [How to deploy](#how-to-deploy)
  - [General deployment instructions](#general-deployment-instructions)
  - [Clouds](#clouds)
  - [Special deployment scenarios](#special-deployment-scenarios)
    - [Networking](#networking)
    - [Airgapped](#airgapped)
    - [Cluster-cluster replication](#cluster-cluster-replication)
    - [Juju storage](#juju-storage)

## General deployment instructions

The basic requirements for deploying a charm are the [**Juju client**](https://juju.is/docs/juju) and a machine [**cloud**](https://juju.is/docs/juju/cloud).

First, [bootstrap](https://juju.is/docs/juju/juju-bootstrap) the cloud controller and create a [model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/): 
```text
juju bootstrap <cloud name> <controller name>
juju add-model <model name>
```

Then, either continue with the `juju` client **or** use the `terraform juju` client to deploy the PostgreSQL charm.

**To deploy with the `juju` client:**
```text
juju deploy postgresql --channel 16/stable -n <number_of_replicas>
```
> See also: [`juju deploy` command](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/deploy/)

**To deploy with `terraform juju`**, follow the guide [How to deploy using Terraform].
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

## Special deployment scenarios

These guides cover some specific deployment scenarios and configurations.

### Networking

[How to deploy for external TLS VIP access] goes over an example deployment of PostgreSQL, PgBouncer and HAcluster that require external TLS/SSL access via [Virtual IP (VIP)](https://en.wikipedia.org/wiki/Virtual_IP_address).

See also:
* [How to enable TLS]
* [How to connect from outside the local network]

[How to deploy on juju spaces] goes over how to configure your deployment of PostgreSQL and client application to use juju spaces to separate network traffic.

### Airgapped
[How to deploy in an offline or air-gapped environment] goes over the special configuration steps for installing PostgreSQL in an airgapped environment via CharmHub and the Snap Store Proxy.

### Cluster-cluster replication
Cluster-cluster, cross-regional, or multi-server asynchronous replication focuses on disaster recovery by distributing data across different servers. 

The [Cross-regional async replication] guide goes through the steps to set up clusters for cluster-cluster replication, integrate with a client, and remove or recover a failed cluster.

### Juju storage
Charmed PostgreSQL uses the [Juju storage](https://documentation.ubuntu.com/juju/3.6/reference/storage/) abstraction to utilize data volume provided by different clouds while keeping the same UI/UX for end users.

See: [How to deploy on juju storage]


<!--Links-->

[Tutorial]: /tutorial/index

[How to deploy using Terraform]: /how-to/deploy/terraform

[Sunbeam]: /how-to/deploy/sunbeam
[Canonical MAAS]: /how-to/deploy/maas
[Amazon Web Services EC2]: /how-to/deploy/aws-ec2
[Google Cloud Engine]: /how-to/deploy/gce
[Azure]: /how-to/deploy/azure
[How to deploy on multiple availability zones (AZ)]: /how-to/deploy/multi-az

[How to deploy for external TLS VIP access]: /how-to/deploy/tls-vip-access
[How to enable TLS]: /how-to/enable-tls
[How to connect from outside the local network]: /how-to/external-network-access
[How to deploy on juju spaces]: /how-to/deploy/juju-spaces

[How to deploy in an offline or air-gapped environment]: /how-to/deploy/air-gapped
[Cross-regional async replication]: /how-to/cross-regional-async-replication/index
[How to deploy on juju storage]: /how-to/deploy/juju-storage


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Sunbeam <sunbeam>
MAAS <maas>
AWS EC2 <aws-ec2>
GCE <gce>
Azure <azure>
Multi-AZ <multi-az>
TLS VIP access <tls-vip-access>
Terraform <terraform>
Air-gapped <air-gapped>
Juju spaces <juju-spaces>
Juju storage <juju-storage>