# How-to guides

The following guides cover key processes and common tasks for managing and using Charmed PostgreSQL on machines.

## Deployment and setup

Installation of different cloud services with Juju:
* [Sunbeam]
* [LXD]
* [MAAS]
* [AWS EC2]
* [GCE]
* [Azure]
* [Multi-availability zones (AZ)][Multi-AZ]

Specific deployment scenarios and architectures:
* [Terraform]
* [Air-gapped]
* [TLS VIP access]

## Usage and maintenance

* [Integrate with another application]
* [External access]
* [Scale replicas]
* [Enable TLS]
* [Enable plugins/extensions]

## Backup and restore
* [Configure S3 AWS]
* [Configure S3 RadosGW]
* [Create a backup]
* [Restore a backup]
* [Manage backup retention]
* [Migrate a cluster]

## Monitoring (COS)

* [Enable monitoring]
* [Enable alert rules]
* [Enable tracing]

## Minor upgrades
* [Perform a minor upgrade]
* [Perform a minor rollback]

## Cross-regional (cluster-cluster) async replication

* [Cross-regional async replication]
    * [Set up clusters]
    * [Integrate with a client app]
    * [Remove or recover a cluster]
    * [Enable plugins/extensions]

## Development

This section is for charm developers looking to support PostgreSQL integrations with their charm.

* [Integrate with your charm]
* [Migrate data via pg_dump]
* [Migrate data via backup/restore]

<!--Links-->

[Sunbeam]: /t/15972
[LXD]: /t/11861
[MAAS]: /t/14293
[AWS EC2]: /t/15703
[GCE]: /t/15722
[Azure]: /t/15733
[Multi-AZ]: /t/15749
[Terraform]: /t/14916
[Air-gapped]: /t/15746
[TLS VIP access]: /t/16576
[Integrate with another application]: /t/9687
[External access]: /t/15802
[Scale replicas]: /t/9689
[Enable TLS]: /t/9685

[Configure S3 AWS]: /t/9681
[Configure S3 RadosGW]: /t/10313
[Create a backup]: /t/9683
[Restore a backup]: /t/9693
[Manage backup retention]: /t/14249
[Migrate a cluster]: /t/9691

[Enable monitoring]: /t/10600
[Enable alert rules]: /t/13084
[Enable tracing]: /t/14521
 
[Perform a minor upgrade]: /t/12089
[Perform a minor rollback]: /t/12090

[Cross-regional async replication]: /t/15412
[Set up clusters]: /t/13991
[Integrate with a client app]: /t/13992
[Remove or recover a cluster]: /t/13994
[Enable plugins/extensions]: /t/10906

[Integrate with your charm]: /t/11865
[Migrate data via pg_dump]: /t/12163
[Migrate data via backup/restore]: /t/12164