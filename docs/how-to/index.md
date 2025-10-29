(how-to)=
# How-to guides

The following guides cover key processes and common tasks for setting up and managing Charmed PostgreSQL on machines.

## Deployment and setup

Available deployment methods and specialised setups:

```{toctree}
:titlesonly:
:maxdepth: 2

Deploy <deploy/index>
```

## Usage and maintenance

Most common operations during the initial setup of a PostgreSQL cluster:

```{toctree}
:titlesonly:

Integrate <integrate-with-another-application>
Scale <scale-replicas>
Manage passwords <manage-passwords>
Enable TLS <enable-tls>
External network access <external-network-access>
Enable LDAP <enable-ldap>
Enable plugins/extensions <enable-plugins-extensions/index>
Switchover/failover <switchover-failover>
```

## Backup and restore

Configuration of storage providers and backup management:

```{toctree}
:titlesonly:
:maxdepth: 2

Back up and restore <back-up-and-restore/index>
```

## Monitoring (COS)

Observability and monitoring with the Canonical Observability Stack:

```{toctree}
:maxdepth: 2

Monitoring (COS) <monitoring-cos/index>
```

## Refresh (upgrade)

Instructions for performing an in-place application refresh:

```{toctree}
:titlesonly:

Refresh (upgrade) <refresh>
```

## Data migration

For charm developers looking to support PostgreSQL integrations with their charm:

```{toctree}
:maxdepth: 2
:titlesonly:

Migrate data <migrate-data/index>
```

## Cross-regional (cluster-cluster) async replication

Walkthrough of a cluster-cluster deployment and its essential operations:

```{toctree}
:maxdepth: 2
:titlesonly:

Cross-regional async replication <cross-regional-async-replication/index>
```

## Logical replication

How to replicate a subset of data to another PostgreSQL cluster:

```{toctree}
:maxdepth: 2
:titlesonly:

Logical replication <logical-replication/index>
```

## Charm development

For charm developers looking to support PostgreSQL integrations with their charm

```{toctree}
:titlesonly:

Integrate PostgreSQL with your charm <integrate-with-your-charm>
```

Other relevant guides:
* {ref}`migrate-data-via-pg-dump`
* {ref}`migrate-data-via-backup-restore`