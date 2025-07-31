---
relatedlinks: "[Charmhub](https://charmhub.io/postgresql?channel=14/stable)"
---

# Charmed PostgreSQL documentation

Charmed PostgreSQL is an open-source software operator designed to deploy and operate object-relational databases on IAAS/VM. It packages the powerful database management system [PostgreSQL](https://www.postgresql.org/) into a charmed operator for deployment with [Juju](https://juju.is/docs/juju).

This charmed operator meets the need of simplifying deployment, scaling, configuration and management of relational databases in large-scale production environments reliably. It is equipped with several features to securely store and scale complicated data workloads, including easy integration with client applications.
 
Charmed PostgreSQL is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.

```{note}
This is a **IAAS/VM** operator. To deploy on Kubernetes, see [Charmed PostgreSQL K8s](https://canonical-charmed-postgresql-k8s.readthedocs-hosted.com/).
```

| | |
|--|--|
|  [**Get started**](/tutorial/index) - [Deploy on a cloud](/how-to/deploy/index) \| [Scale](/how-to/scale-replicas) \| [Manage passwords](/how-to/manage-passwords) \| [Enable encryption](/how-to/enable-tls) \| [Back up](/how-to/back-up-and-restore/index) \| [Monitoring](/how-to/monitoring-cos/index) </br> |  [**How-to guides**](/how-to/index) for key tasks, use-cases, and problems. These guides assume basic familiarity with Juju and PostgreSQL. </br>  |
| [**Reference**](/reference/index) - Technical information for quick lookup, such as [requirements](/reference/system-requirements), [plugins](/reference/plugins-extensions), and [statuses](/reference/statuses). | [**Explanation**](/explanation/interfaces-and-endpoints) - Discussion and clarification of key topics such as [architecture](/explanation/architecture), [users](/explanation/users), and [legacy charms](/explanation/legacy-charm)|


## Project and community

Charmed PostgreSQL is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.
- [Read our Code of Conduct](https://ubuntu.com/community/code-of-conduct)
- [Join the Discourse forum](https://discourse.charmhub.io/tag/postgresql)
- [Contribute](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md) to the code or report an [issue](https://github.com/canonical/postgresql-operator/issues/new/choose)
- Explore [Canonical Data solutions](https://canonical.com/data)
- [Contacts us](/reference/contacts) for all further questions

## Licensing & Trademark

The Charmed PostgreSQL Operator is distributed under the [Apache Software Licence version 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE). It depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which is licensed under the [PostgreSQL License](https://www.postgresql.org/about/licence/) - a liberal open-source licence similar to the BSD or MIT licences.

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are the property of their respective owners.


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Home <self>
Tutorial <tutorial/index>
How-to guides <how-to/index>
Reference <reference/index>
Explanation <explanation/index>
```
