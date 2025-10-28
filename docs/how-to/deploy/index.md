# How to deploy

The basic requirements for deploying a charm are the [**Juju client**](https://documentation.ubuntu.com/juju/3.6/) and a machine [**cloud**](https://juju.is/docs/juju/cloud).

For more details, see {ref}`system-requirements`.

If you are not sure where to start, or would like a more guided walkthrough for setting up your environment, see the {ref}`tutorial`.

## Quickstart

First, [bootstrap](https://juju.is/docs/juju/juju-bootstrap) the cloud controller and create a [model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/): 

```shell
juju bootstrap <cloud name> <controller name>
juju add-model <model name>
```

Then, use the [`juju deploy`](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/deploy/) command:

```shell
juju deploy postgresql --channel 16/stable -n <number_of_replicas>
```

(deploy-clouds)=
## Clouds

Set up different cloud services for a Charmed PostgreSQL deployment:

```{toctree}
:titlesonly:

Sunbeam <sunbeam>
MAAS <maas>
AWS EC2 <aws-ec2>
GCE <gce>
Azure <azure>
```

Deploy a cluster on a cloud using different availability zones:

```{toctree}
:titlesonly:

Multi-AZ <multi-az>
```

## Terraform

```{toctree}
:titlesonly:

Terraform <terraform>
```

## Networking and TLS encryption

Basic instructions about enabling TLS with Charmed PostgreSQL:

* {ref}`enable-tls`.

Example setup of external TLS/SSL access via Virtual IP (VIP):

```{toctree}
:titlesonly:

TLS VIP access <tls-vip-access>
```

Configure Juju spaces to separate network traffic:

```{toctree}
:titlesonly:

Juju spaces <juju-spaces>
```

## Airgapped

Install PostgreSQL in an airgapped environment via Charmhub and the Snap Store Proxy:

```{toctree}
:titlesonly:

Air-gapped <air-gapped>
```

## Juju storage

Use volume provided by different clouds via [Juju storage](https://documentation.ubuntu.com/juju/3.6/reference/storage/):

```{toctree}
:titlesonly:

Juju storage <juju-storage>
```
