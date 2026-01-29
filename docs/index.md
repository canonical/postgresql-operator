---
relatedlinks: "[Charmhub](https://charmhub.io/postgresql?channel=16/stable)"
---

# Charmed PostgreSQL documentation

Charmed PostgreSQL is an open-source operator designed to deploy and operate PostgreSQL on virtual machines and cloud services. It packages the relational database management system [PostgreSQL](https://www.postgresql.org/) with the [Patroni](https://patroni.readthedocs.io/en/latest/) high-availability replication system into an operator for deployment with [Juju](https://juju.is/docs/juju).

This charmed operator simplifies deployment, scaling, configuration and management of PostgreSQL databases in large-scale production environments reliably. It is equipped with several features to securely store and scale complicated data workloads, including easy integration with client applications.
 
Charmed PostgreSQL is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.

```{note}
This is a **IAAS/VM** operator. To deploy on Kubernetes, see [Charmed PostgreSQL K8s](https://canonical-charmed-postgresql-k8s.readthedocs-hosted.com/).
```

## In this documentation

### Get started

Learn about what's in the charm, how to set up your environment, and perform the most common operations.

* **Charm overview**: {ref}`architecture` • {ref}`system-requirements` • {ref}`Charm versions <charm-versions>`
* **Deploy PostgreSQL**: {ref}`Guided tutorial <tutorial>` • {ref}`deploy-quickstart` • {ref}`Set up a cloud <deploy-clouds>`
* **Key operations**: {ref}`Scale your cluster <scale-replicas>` • {ref}`Manage user credentials <manage-passwords>` • {ref}`Create a backup <create-a-backup>`

### Production deployments

Advanced deployments and operations focused on production scenarios and high availability.

* **Advanced deployment scenarios**: {ref}`Terraform <terraform>` • {ref}`Air-gapped deployments <air-gapped>` • {ref}`Multiple availability zones <multi-az>` • {ref}`Cluster-cluster replication <cross-regional-async-replication>` • {ref}`Logical replication <logical-replication>`
* **Networking**: {ref}`Juju spaces <juju-spaces>` • {ref}`Enable TLS encryption <enable-tls>` • {ref}`External network access <external-network-access>`
* **Upgrades and data migration**: {ref}`In-place refresh (upgrade) <refresh>` • {ref}`Cluster and data migration <data-migration>`
* **Troubleshooting**: {ref}`Overview and tools <troubleshooting>` • {ref}`Manual switchover/failover <switchover-failover>` • {ref}`Logs<logs>` • {ref}`sos-report`

### Charm developers

* **Make your charm compatible with PostgreSQL**: {ref}`Interfaces and endpoints <interfaces-and-endpoints>` • {ref}`How to integrate with your charm with PostgreSQL <integrate-with-your-charm>`
* **Learn more about the charm**: {ref}`Internal users <users>` • {ref}`Roles <roles>` • {ref}`Charm versions <charm-versions>`
* **Juju properties**: [Configuration parameters](https://charmhub.io/postgresql/configurations?channel=16/stable) • [Actions](https://charmhub.io/postgresql/actions?channel=16/stable)

## How this documentation is organised

This documentation uses the [Diátaxis documentation structure](https://diataxis.fr/):

* The {ref}`tutorial` provides step-by-step guidance for a beginner through the basics of a deployment in a local machine.
* {ref}`how-to` are more focused, and assume you already have basic familiarity with the product.
* {ref}`reference` contains structured information for quick lookup, such as system requirements and configuration parameters
* {ref}`explanation` gives more background and context about key topics


## Project and community

Charmed PostgreSQL is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.

### Get involved

* [Discourse forum](https://discourse.charmhub.io/tag/postgresql)
* [Public Matrix channel](https://matrix.to/#/#charmhub-data-platform:ubuntu.com)
* [Report an issue](https://github.com/canonical/postgresql-operator/issues/new/choose)
* [Contribute](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md)

### Governance and policies

- [Code of Conduct](https://ubuntu.com/community/code-of-conduct)

## Licensing & trademark

The Charmed PostgreSQL Operator is distributed under the [Apache Software Licence version 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE). It depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which is licensed under the [PostgreSQL License](https://www.postgresql.org/about/licence/) - a liberal open-source licence similar to the BSD or MIT licences.

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are the property of their respective owners.


```{toctree}
:titlesonly:
:hidden:

Home <self>
tutorial
how-to/index
reference/index
explanation/index
```