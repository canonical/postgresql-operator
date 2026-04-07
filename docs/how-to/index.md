(how-to)=
# How-to guides

The following guides cover key processes and common tasks for setting up and managing Charmed PostgreSQL on bare metal and virtual machines.

## Deployment and setup

Available deployment methods and specialised setups:

```{toctree}
:titlesonly:
:maxdepth: 2

Deploy <deploy/index>
```

## Operations and maintenance

Essential operations to configure and manage a PostgreSQL cluster:

```{toctree}
:titlesonly:

Scale <scale-replicas>
Integrate <integrate-with-another-application>
Manage passwords <manage-passwords>
Enable TLS <enable-tls>
Enable plugins/extensions <enable-plugins-extensions/index>
```

Advanced networking, credential management, and disaster recovery:

```{toctree}
:titlesonly:

External network access <external-network-access>
Enable LDAP <enable-ldap>
Switchover/failover <switchover-failover>
```

### Backups and data migration

Configuration of storage providers and backup management for safety and data migration from a different cluster:

```{toctree}
:titlesonly:
:maxdepth: 2

Back up and restore <back-up-and-restore/index>
```

Other data migration guides:

```{toctree}
:titlesonly:
:maxdepth: 2

Data migration <data-migration/index>
```

### Monitoring (COS)

Set up observability services like Grafana, Prometheus, Loki, and Tempo through the Canonical Observability Stack (COS):

```{toctree}
:maxdepth: 2

Monitoring (COS) <monitoring-cos/index>
```

### Refresh (upgrade)

Instructions for performing an in-place application refresh:

```{toctree}
:titlesonly:

Refresh (upgrade) <refresh>
```

### Cross-regional (cluster-cluster) async replication

Walkthrough of a cluster-cluster deployment and its essential operations:

```{toctree}
:maxdepth: 2
:titlesonly:

Cross-regional async replication <cross-regional-async-replication/index>
```

### Logical replication

Replicate a subset of data to another PostgreSQL cluster:

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