# Cross-regional async replication

Cross-regional (or multi-server) asynchronous replication focuses on disaster recovery by distributing data across different servers.

## Prerequisites
* Juju `v.3.4.2+`
* Make sure your machine(s) fulfill the [system requirements](/t/11743)

### Substrate dependencies

The following table shows the source and target controller/model combinations that are currently supported:

|  | AWS | GCP | Azure |
|---|---|:---:|:---:|
| AWS |  |  |  |
| GCP |  | ![ check ] | ![ check ] |
| Azure |  | ![ check ] | ![ check ] |

## How-to guides

* [How to set up clusters for cross-regional async replication](/t/13991)
* [How to integrate with a client application](/t/13992)
* [How to remove or recover a cluster](/t/13994)
  * [Switchover](/t/13994#switchover)
  * [Detach](/t/13994#detach-a-cluster)
  * [Recover](/t/13994#recover-a-cluster)

<!-- BADGES -->
[check]: https://img.shields.io/badge/%E2%9C%93-brightgreen
[cross]: https://img.shields.io/badge/x-white