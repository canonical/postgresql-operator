# Cross-regional async replication

Cross-regional (or multi-server) asynchronous replication focuses on disaster recovery by distributing data across different servers.

## Prerequisites
* Juju `v.3.4.2+`
* Make sure your machine(s) fulfil the [system requirements](/reference/system-requirements)

### Substrate dependencies

The following table shows the source and target controller/model combinations that are currently supported:

|  | AWS | GCP | Azure |
|---|---|:---:|:---:|
| AWS |  |  |  |
| GCP |  | ![ check ] | ![ check ] |
| Azure |  | ![ check ] | ![ check ] |

## Guides

```{toctree}
:titlesonly:
:maxdepth: 2

Set up clusters <set-up-clusters>
Integrate with a client app <integrate-with-a-client-app>
Remove or recover a cluster <remove-or-recover-a-cluster>
```
<!-- BADGES -->
[check]: https://img.shields.io/badge/%E2%9C%93-brightgreen
[cross]: https://img.shields.io/badge/x-white


