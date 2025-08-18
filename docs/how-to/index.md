# How-to guides

The following guides cover key processes and common tasks for managing and using Charmed PostgreSQL on machines.

## Deployment and setup

Installation of different cloud services with Juju:
* [Sunbeam]
* [MAAS]
* [AWS EC2]
* [GCE]
* [Azure]
* [Multi-availability zones (AZ)][Multi-AZ]

Other deployment scenarios and configurations:
* [Terraform]
* [TLS VIP access]
* [Air-gapped]
* [Juju spaces]
* [Juju storage]

## Usage and maintenance

* [Integrate with another application]
* [External access]
* [Scale replicas]
* [Enable TLS]
* [Enable plugins/extensions]
* [Switchover/failover]

## Backup and restore
* [Configure S3 AWS]
* [Configure S3 RadosGW]
* [Create a backup]
* [Restore a backup]
* [Manage backup retention]
* [Migrate a cluster]

## Monitoring (COS)

* [Enable monitoring] with Grafana
* [Enable alert rules] with Prometheus
* [Enable tracing] with Tempo
* [Enable profiling] with Parca

## Refresh (upgrade)
* [How to refresh]
    * [Perform a minor upgrade]
    * [Roll back an in-progress refresh]

## Cross-regional (cluster-cluster) async replication

* [Cross-regional async replication]
    * [Set up clusters]
    * [Integrate with a client app]
    * [Remove or recover a cluster]
    * [Enable plugins/extensions]

## Logical replication
* [Logical replication]
    * [Set up two clusters]
    * [Re-enable logical replication]

## Development

This section is for charm developers looking to support PostgreSQL integrations with their charm.

* [Integrate with your charm]
* [Migrate data via pg_dump]
* [Migrate data via backup/restore]

<!--Links-->

[Sunbeam]: /how-to/deploy/sunbeam
[MAAS]: /how-to/deploy/maas
[AWS EC2]: /how-to/deploy/aws-ec2
[GCE]: /how-to/deploy/gce
[Azure]: /how-to/deploy/azure
[Multi-AZ]: /how-to/deploy/multi-az
[TLS VIP access]: /how-to/deploy/tls-vip-access
[Juju spaces]: /how-to/deploy/juju-spaces
[Terraform]: /how-to/deploy/terraform
[Air-gapped]: /how-to/deploy/air-gapped
[Juju storage]: /how-to/deploy/juju-storage

[Integrate with another application]: /how-to/integrate-with-another-application
[External access]: /how-to/external-network-access
[Scale replicas]: /how-to/scale-replicas
[Enable TLS]: /how-to/enable-tls
[Switchover/failover]: /how-to/switchover-failover

[Configure S3 AWS]: /how-to/back-up-and-restore/configure-s3-aws
[Configure S3 RadosGW]: /how-to/back-up-and-restore/configure-s3-radosgw
[Create a backup]: /how-to/back-up-and-restore/create-a-backup
[Restore a backup]: /how-to/back-up-and-restore/restore-a-backup
[Manage backup retention]: /how-to/back-up-and-restore/manage-backup-retention
[Migrate a cluster]: /how-to/back-up-and-restore/migrate-a-cluster

[Enable monitoring]: /how-to/monitoring-cos/enable-monitoring
[Enable alert rules]: /how-to/monitoring-cos/enable-alert-rules
[Enable tracing]: /how-to/monitoring-cos/enable-tracing
[Enable profiling]: /how-to/monitoring-cos/enable-profiling

[How to upgrade]: /how-to/refresh/index
[Perform a minor upgrade]: /how-to/refresh/minor-upgrade
[Roll back an in-progress refresh]: /how-to/refresh/rollback

[Cross-regional async replication]: /how-to/cross-regional-async-replication/index
[Set up clusters]: /how-to/cross-regional-async-replication/set-up-clusters
[Integrate with a client app]: /how-to/cross-regional-async-replication/integrate-with-a-client-app
[Remove or recover a cluster]: /how-to/cross-regional-async-replication/remove-or-recover-a-cluster
[Enable plugins/extensions]: /how-to/enable-plugins-extensions/index

[Logical replication]: /how-to/logical-replication/index
[Set up two clusters]: /how-to/logical-replication/set-up-clusters
[Re-enable logical replication]: /how-to/logical-replication/re-enable

[Integrate with your charm]: /how-to/development/integrate-with-your-charm
[Migrate data via pg_dump]: /how-to/development/migrate-data-via-pg-dump
[Migrate data via backup/restore]: /how-to/development/migrate-data-via-backup-restore


```{toctree}
:titlesonly:
:maxdepth: 2
:hidden:

Deploy <deploy/index>
Integrate <integrate-with-another-application>
Manage passwords <manage-passwords>
External network access <external-network-access>
Scale <scale-replicas>
Switchover/failover <switchover-failover>
Enable TLS <enable-tls>
Enable LDAP <enable-ldap>
Enable plugins/extensions <enable-plugins-extensions/index>
Back up and restore <back-up-and-restore/index>
Monitoring (COS) <monitoring-cos/index>
Refresh (upgrade) <refresh/index>
Cross-regional async replication <cross-regional-async-replication/index>
Logical replication <logical-replication/index>
Development <development/index>
```

