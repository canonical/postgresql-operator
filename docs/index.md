# Charmed PostgreSQL documentation

```{note}
This is a **IAAS/VM** operator. To deploy on Kubernetes, see [Charmed PostgreSQL K8s](https://canonical-charmed-postgresql-k8s.readthedocs-hosted.com/).
```

Charmed PostgreSQL is an open-source software operator designed to deploy and operate object-relational databases on IAAS/VM. It packages the powerful database management system [PostgreSQL](https://www.postgresql.org/) into a charmed operator for deployment with [Juju](https://juju.is/docs/juju).

This charm offers automated operations management from day 0 to day 2. It is equipped with several features to securely store and scale complicated data workloads, including TLS encryption, backups, monitoring, password rotation, and easy integration with client applications.

Charmed PostgreSQL meets the need of deploying PostgreSQL in a structured and consistent manner while providing flexibility in configuration. It simplifies deployment, scaling, configuration and management of relational databases in large-scale production environments reliably.
 
This charmed operator is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.

<!-- 
This "Charmed PostgreSQL" operator (in the channel `14/stable`) is a new "[Charmed SDK](https://juju.is/docs/sdk)"-based charm to replace legacy "[Reactive](https://juju.is/docs/sdk/charm-taxonomy#reactive)"-based charm (in the channel `latest/stable`). <br/>Read more about [legacy charm here](/explanation/legacy-charm).
-->

## In this documentation

| | |
|--|--|
|  [**Tutorials**](/tutorial/index)</br>  [Get started](/tutorial/index) - a hands-on introduction to using Charmed PostgreSQL operator for new users </br> |  [**How-to guides**](/how-to-guides/scale-replicas) </br> Step-by-step guides covering key operations such as [scaling](/how-to-guides/scale-replicas), [encryption](/how-to-guides/enable-tls), and [restoring backups](/how-to-guides/back-up-and-restore/restore-a-backup) |
| [**Reference**](/reference/index) </br> Technical information such as [requirements](/reference/system-requirements), [release notes](/reference/releases), and [plugins](/reference/plugins-extensions) | [**Explanation**](/explanation/interfaces-and-endpoints) </br> Concepts - discussion and clarification of key topics such as [architecture](/explanation/architecture), [users](/explanation/users), and [legacy charms](/explanation/legacy-charm)|
## Project and community

Charmed PostgreSQL is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.
- [Read our Code of Conduct](https://ubuntu.com/community/code-of-conduct)
- [Join the Discourse forum](https://discourse.charmhub.io/tag/postgresql)
- [Contribute](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md) to the code or report an [issue](https://github.com/canonical/postgresql-operator/issues/new/choose)
- Explore [Canonical Data Fabric solutions](https://canonical.com/data)
- [Contacts us](/reference/contacts) for all further questions

## Licencing & Trademark
The Charmed PostgreSQL Operator is distributed under the [Apache Software Licence version 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE). It depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which is licensed under the [PostgreSQL License](https://www.postgresql.org/about/licence/) - a liberal open-source licence similar to the BSD or MIT licences.

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are the property of their respective owners.


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Home <self>
tutorial*/index
how*/index
reference*/index
explanation*/index
*
