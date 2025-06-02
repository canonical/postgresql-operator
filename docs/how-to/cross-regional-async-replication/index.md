# Cross-regional async replication

Cross-regional (or multi-server) asynchronous replication focuses on disaster recovery by distributing data across different servers.

## Prerequisites
* Juju `v.3.4.2+`
* Make sure your machine(s) fulfill the [system requirements](/reference/system-requirements)

### Substrate dependencies

The following table shows the source and target controller/model combinations that are currently supported:

|  | AWS | GCP | Azure |
|---|---|:---:|:---:|
| AWS |  |  |  |
| GCP |  | ![ check ] | ![ check ] |
| Azure |  | ![ check ] | ![ check ] |

## Guides

* [How to set up clusters for cross-regional async replication](/how-to/cross-regional-async-replication/set-up-clusters)
* [How to integrate with a client application](/how-to/cross-regional-async-replication/integrate-with-a-client-app)
* [How to remove or recover a cluster](/how-to/cross-regional-async-replication/remove-or-recover-a-cluster)
  * [Switchover](/how-to/cross-regional-async-replication/remove-or-recover-a-cluster)
  * [Detach](/how-to/cross-regional-async-replication/remove-or-recover-a-cluster)
  * [Recover](/how-to/cross-regional-async-replication/remove-or-recover-a-cluster)

<!-- BADGES -->
[check]: https://img.shields.io/badge/%E2%9C%93-brightgreen
[cross]: https://img.shields.io/badge/x-white


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Set up clusters <set-up-clusters>
Integrate with a client app <integrate-with-a-client-app>
Remove or recover a cluster <remove-or-recover-a-cluster>
