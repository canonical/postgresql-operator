> This is a **IAAS/VM** operator. To deploy on Kubernetes, see [Charmed PostgreSQL K8s](https://charmhub.io/postgresql-k8s).

# Charmed PostgreSQL documentation

Charmed PostgreSQL is an open-source software operator designed to deploy and operate object-relational databases on IAAS/VM. It packages the powerful database management system [PostgreSQL](https://www.postgresql.org/) into a charmed operator for deployment with [Juju](https://juju.is/docs/juju).

This charm offers automated operations management from day 0 to day 2. It is equipped with several features to securely store and scale complicated data workloads, including TLS encryption, backups, monitoring, password rotation, and easy integration with client applications.

Charmed PostgreSQL meets the need of deploying PostgreSQL in a structured and consistent manner while providing flexibility in configuration. It simplifies deployment, scaling, configuration and management of relational databases in large-scale production environments reliably.
 
This charmed operator is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.

<!-- 
This "Charmed PostgreSQL" operator (in the channel `14/stable`) is a new "[Charmed SDK](https://juju.is/docs/sdk)"-based charm to replace legacy "[Reactive](https://juju.is/docs/sdk/charm-taxonomy#heading--reactive)"-based charm (in the channel `latest/stable`). <br/>Read more about [legacy charm here](/t/10690).
-->

## In this documentation

| | |
|--|--|
|  [**Tutorials**](/t/9707)</br>  [Get started](/t/9707) - a hands-on introduction to using Charmed PostgreSQL operator for new users </br> |  [**How-to guides**](/t/9689) </br> Step-by-step guides covering key operations such as [scaling](/t/9689), [encryption](/t/9685), and [restoring backups](/t/9693) |
| [**Reference**](/t/13976) </br> Technical information such as [requirements](/t/11743), [release notes](/t/11875), and [plugins](/t/10946) | [**Explanation**](/t/10251) </br> Concepts - discussion and clarification of key topics such as [architecture](/t/11857), [users](/t/10798), and [legacy charms](/t/10690)|
## Project and community

Charmed PostgreSQL is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.
- [Read our Code of Conduct](https://ubuntu.com/community/code-of-conduct)
- [Join the Discourse forum](https://discourse.charmhub.io/tag/postgresql)
- [Contribute](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md) to the code or report an [issue](https://github.com/canonical/postgresql-operator/issues/new/choose)
- Explore [Canonical Data Fabric solutions](https://canonical.com/data)
- [Contacts us](/t/11863) for all further questions

## Licencing & Trademark
The Charmed PostgreSQL Operator is distributed under the [Apache Software Licence version 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE). It depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which is licensed under the [PostgreSQL License](https://www.postgresql.org/about/licence/) - a liberal open-source licence similar to the BSD or MIT licences.

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are the property of their respective owners.


# Navigation

[details=Navigation]

| Level | Path | Navlink |
|--------|--------|-------------|
| 1 | tutorial | [Tutorial](/t/9707) |
| 2 | t-set-up | [1. Set up environment](/t/9709) |
| 2 | t-deploy | [2. Deploy PostgreSQL](/t/9697) |
| 2 | t-access| [3. Access PostgreSQL](/t/15798) |
| 2 | t-scale | [4. Scale replicas](/t/9705) |
| 2 | t-manage-passwords | [5. Manage passwords](/t/9703) |
| 2 | t-integrate | [6. Integrate with other applications](/t/9701) |
| 2 | t-enable-tls | [7. Enable TLS encryption](/t/9699) |
| 2 | t-clean-up | [8. Clean up environment](/t/9695) |
| 1 | how-to | [How-to guides](/t/16766) |
| 2 | h-deploy | [Deploy](/t/16811) |
| 3 | h-deploy-sunbeam | [Sunbeam](/t/15972) |
| 3 | h-deploy-maas | [MAAS](/t/14293) |
| 3 | h-deploy-ec2 | [AWS EC2](/t/15703) |
| 3 | h-deploy-gce | [GCE](/t/15722) |
| 3 | h-deploy-azure | [Azure](/t/15733) |
| 3 | h-deploy-multi-az | [Multi-AZ](/t/15749) |
| 3 | h-deploy-terraform | [Terraform](/t/14916) |
| 3 | h-deploy-airgapped | [Air-gapped](/t/15746) |
| 3 | h-deploy-tls-vip-access | [TLS VIP access](/t/16576) |
| 2 | h-integrate | [Integrate with another application](/t/9687) |
| 2 | h-external-access | [External network access](/t/15802) |
| 2 | h-scale | [Scale replicas](/t/9689) |
| 2 | h-enable-tls | [Enable TLS](/t/9685) |
| 2 | h-enable-plugins-extensions | [Enable plugins/extensions](/t/10906) |
| 2 | h-backup | [Back up and restore]() |
| 3 | h-configure-s3-aws | [Configure S3 AWS](/t/9681) |
| 3 | h-configure-s3-radosgw | [Configure S3 RadosGW](/t/10313) |
| 3 | h-create-backup | [Create a backup](/t/9683) |
| 3 | h-restore-backup | [Restore a backup](/t/9693) |
| 3 | h-manage-backup-retention | [Manage backup retention](/t/14249) |
| 3 | h-migrate-cluster | [Migrate a cluster](/t/9691) |
| 2 | h-monitor | [Monitoring (COS)]() |
| 3 | h-enable-monitoring | [Enable monitoring](/t/10600) |
| 3 | h-enable-alert-rules | [Enable alert rules](/t/13084) |
| 3 | h-enable-tracing | [Enable tracing](/t/14521) |
| 3 | h-enable-profiling | [Enable profiling](/t/17172) |
| 2 | h-upgrade | [Upgrade](/t/12086) |
| 3 | h-upgrade-minor | [Perform a minor upgrade](/t/12089) |
| 3 | h-rollback-minor | [Perform a minor rollback](/t/12090) |
| 2 | h-async | [Cross-regional async replication](/t/15412) |
| 3 | h-async-set-up | [Set up clusters](/t/13991) |
| 3 | h-async-integrate | [Integrate with a client app](/t/13992) |
| 3 | h-async-remove-recover | [Remove or recover a cluster](/t/13994) |
| 2 | h-development| [Development]() |
| 3 | h-development-integrate | [Integrate with your charm](/t/11865) |
| 3 | h-migrate-pgdump | [Migrate data via pg_dump](/t/12163) |
| 3 | h-migrate-backup-restore | [Migrate data via backup/restore](/t/12164) |
| 1 | reference | [Reference](/t/13976) |
| 2 | r-releases | [Releases](/t/11875) |
| 2 | r-system-requirements | [System requirements](/t/11743) |
| 2 | r-software-testing | [Software testing](/t/11773) |
| 2 | r-performance | [Performance and resources](/t/11974) |
| 2 | r-troubleshooting | [Troubleshooting](/t/11864) |
| 3 | r-sos-report | [SOS report](/t/17228) |
| 2 | r-plugins-extensions | [Plugins/extensions](/t/10946) |
| 2 | r-alert-rules | [Alert rules](/t/15841) |
| 2 | r-statuses | [Statuses](/t/10844) |
| 2 | r-contacts | [Contacts](/t/11863) |
| 1 | explanation | [Explanation](/t/16768) |
| 2 | e-architecture | [Architecture](/t/11857) |
| 2 | e-interfaces-endpoints | [Interfaces and endpoints](/t/10251) |
| 2 | e-juju-details | [Juju](/t/11985) |
| 2 | e-legacy-charm | [Legacy charm](/t/10690) |
| 2 | e-connection-pooling| [Connection pooling](/t/15777) |
| 2 | e-users | [Users](/t/10798) |
| 2 | e-logs | [Logs](/t/12099) |
| 2 | e-security | [Security](/t/16852) |
| 3 | e-cryptography | [Cryptography](/t/16853) |
| 1 | search | [Search](https://canonical.com/data/docs/postgresql/iaas) |

[/details]

<!--Removed from navigation - archived.
| 3 | h-upgrade-major | [Perform a major upgrade](/t/12087) |
| 3 | h-rollback-major | [Perform a major rollback](/t/12088) |

| 3 | h-deploy-lxd | [LXD](/t/11861) |

| 3 | r-revision-552-553| [Revision 552/553](/t/16007) |
| 3 | r-revision-467-468 | [Revision 467/468](/t/15378) |
| 3 | r-revision-429-430 | [Revision 429/430](/t/14067) |
| 3 | r-revision-363 | [Revision 363](/t/13124) |
| 3 | r-revision-351 | [Revision 351](/t/12823) |
| 3 | r-revision-336 | [Revision 336](/t/11877) |
| 3 | r-revision-288 | [Revision 288](/t/11876) |
-->