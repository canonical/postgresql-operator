# Architecture

[Charmed PostgreSQL](https://charmhub.io/postgresql) is a Juju-based operator to deploy and operate [PostgreSQL](https://www.postgresql.org/).It is based on [PostgreSQL Community Edition](https://www.postgresql.org/community/), and uses [Patroni](https://github.com/zalando/patroni) to manage PostgreSQL cluster via [synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-synchronous-replication).

```{figure} architecture-diagram.png
:width: 690
:alt: Diagram of a sample PostgreSQL deployment with 3 units, and integrations with PgBouncer, Prometheus, Grafana, and Ceph.

Example Charmed PostgreSQL deployment with 3 replicas. Integrations include PgBouncer for load balancing, Prometheus and Grafana for observability, and Ceph for backups.
```

## High-level design

The charm design leverages on the [charmed-postgresql](https://snapcraft.io/charmed-postgresql) snap, which is deployed by Juju on the specified VM/MAAS/bare-metal machine based on Ubuntu Noble/24.04. The snap allows to run PostgreSQL service(s) in a secure and isolated environment. For more information, see this blog post about [strict confinement](https://ubuntu.com/blog/demystifying-snap-confinement). 

```{note}
The charmed-postgresql snap installs `14/stable` by default.

For `16/edge`, use the `--channel` flag when installing or refreshing the snap.
```

<!-- TODO: update sample output for 16/edge snap
```shell
juju ssh postgresql/0 snap list charmed-postgresql
Name                Version  Rev  Tracking       Publisher        Notes
charmed-postgresql  16.10    TODO TODO/edge      dataplatformbot  held
```
-->

The snap ships the following components:

* **PostgreSQL** (based on Ubuntu APT package [postgresql](https://packages.ubuntu.com/jammy/postgresql)) 
* **PgBouncer** (based on Canonical [backport](https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer))
* **Patroni** (based on Canonical [backport](https://launchpad.net/~data-platform/+archive/ubuntu/patroni))
* **pgBackRest** (based on Canonical  [backport](https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest))
* **Prometheus PostgreSQL Exporter** (based on Canonical [backport](https://launchpad.net/~data-platform/+archive/ubuntu/postgres-exporter))
* **Prometheus PgBouncer Exporter** (based on Canonical [backport](https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer-exporter))
* Prometheus Grafana dashboards and Loki alert rules are part of the charm revision (and missing in the snap).

Versions of all the components above are carefully chosen to fit functionality of each other.

The Charmed PostgreSQL unit consisting of a several services which are enabled/activated according to the setup: 

```shell
$ juju ssh postgresql/0 snap services charmed-postgresql

Service                                          Startup   Current  Notes
charmed-postgresql.ldap-sync                     enabled   active   -
charmed-postgresql.patroni                       enabled   active   -
charmed-postgresql.pgbackrest-exporter           enabled   active   -
charmed-postgresql.pgbackrest-service            enabled   active   -
charmed-postgresql.prometheus-postgres-exporter  enabled   active   -
charmed-postgresql.pgbackrest-exporter           enabled   active   -
```

<!-- TODO: The `ldap-sync` service is... -->

The `patroni` snap service is a main PostgreSQL instance which is normally up and running right after the charm deployment.

The `pgbackrest` snap service is a backup framework for PostgreSQL. It is disabled if [Backup](/how-to/back-up-and-restore/create-a-backup) is not configured along with the `pgbackrest-exporter` service.

The `prometheus-postgres-exporter` service is activated after the relation with [COS Monitoring](/how-to/monitoring-cos/enable-monitoring) only.

```{caution}
It is **not recommended** to start, stop, and restart snap services manually in order to avoid a split-brain scenario with a charm state machine.
```

The charmed-postgresql snap also ships list of tools used by charm:
* `charmed-postgresql.psql` (alias `psq`) - is PostgreSQL interactive terminal.
* `charmed-postgresql.patronictl` - a tool to monitor and manage Patroni.
* `charmed-postgresql.pgbackrest` - a tool to backup/restore PostgreSQL DB.

```{warning}
All snap resources must be executed under the special snap user `_daemon_` only!
```

## Integrations

The following charms can be integrated with PostgreSQL out of the box.  

### PgBouncer

[PgBouncer](http://www.pgbouncer.org/) is a lightweight connection pooler for PostgreSQL that provides transparent routing between your application and back-end PostgreSQL Servers. 

### TLS certificates operator

The [TLS Certificates](https://charmhub.io/tls-certificates-operator) charm is responsible for distributing certificates through Juju configs. For test deployments, the [self-signed certificates](https://charmhub.io/self-signed-certificates) charm is available as well.

See also: [](/how-to/enable-tls)

### S3 integrator

[S3 Integrator](https://charmhub.io/s3-integrator) is an integrator charm for providing S3 credentials to Charmed PostgreSQL to access shared S3 data. Store the credentials centrally in the integrator charm and relate consumer charms as needed.

See also: [](configure-s3-integrator-aws)

### Data integrator

The [data integrator](https://charmhub.io/data-integrator) charm requests credentials for non-native Juju applications. Not all applications implement a `data_interfaces` relation, but do allow setting credentials via config. Additionally, some of applications are run outside of juju. This integrator charm allows receiving credentials which can be passed into application config directly without implementing a Juju-native relation.

See also: [](/how-to/integrate-with-another-application)

### PostgreSQL test app

The [PostgreSQL Test App](https://charmhub.io/postgresql-test-app) charm is a Canonical test application to validate the charm installation / functionality and perform basic performance tests.

### GLAuth

[GLAuth](https://github.com/glauth/glauth) is a secure, easy-to-use and open-sourced LDAP server which provides capabilities to centrally manage accounts across infrastructures. The charm is only available for Kubernetes clouds via the [GLAuth-K8s operator](https://charmhub.io/glauth-k8s), so a cross-controller relation is needed in order to integrate both charms.

### Grafana

[Grafana](https://grafana.com/) is an open-source visualisation tools that allows to query, visualise, alert on, and visualise metrics from mixed data sources in configurable dashboards for observability. This charms is shipped with its own Grafana dashboard and supports integration with the [Grafana Operator](https://charmhub.io/grafana-k8s) to simplify observability.

See also: [](/how-to/monitoring-cos/enable-monitoring) 

#### Loki

[Loki](https://grafana.com/docs/loki/latest/) is an open-source fully-featured logging system. This charms is shipped with support for the [Loki Operator](https://charmhub.io/loki-k8s) to collect the generated logs. 

See also: [](/how-to/monitoring-cos/enable-monitoring) 

### Prometheus

[Prometheus](https://prometheus.io/docs/introduction/overview/) is an open-source systems monitoring and alerting toolkit with a dimensional data model, flexible query language, efficient time series database and modern alerting approach. Charmed PostgreSQL is shipped with a Prometheus exporters, alerts and support for integrating with the [Prometheus charm](https://charmhub.io/prometheus-k8s) to automatically scrape the targets. 

See also: [](/how-to/monitoring-cos/enable-monitoring) 

## LLD (Low Level Design)

Please check the charm state machines displayed in the [workflow diagrams](https://discourse.charmhub.io/t/charmed-postgresql-k8s-explanations-charm-flowcharts/9305). The low-level logic is mostly common for both VM and K8s charms.

<!--- TODO: Describe all possible installations? Cross-model/controller? --->

### Juju events

Accordingly to the [Juju SDK](https://juju.is/docs/sdk/event): “an event is a data structure that encapsulates part of the execution context of a charm”.

For this charm, the following events are observed:

1. [`on_install`](https://documentation.ubuntu.com/juju/3.6/reference/hook/#install): install the snap "charmed-postgresql" and perform basic preparations to bootstrap the cluster on the first leader (or join the already configured cluster). 
2. [`leader-elected`](https://documentation.ubuntu.com/juju/3.6/reference/hook/#leader-elected): generate all the secrets to bootstrap the cluster.
3. [`leader-settings-changed`](https://documentation.ubuntu.com/juju/3.6/reference/hook/#leader-settings-changed): Handle the leader settings changed event.
4. [`start`](https://documentation.ubuntu.com/juju/3.6/reference/hook/#start): Init/setting up the cluster node.
5. [`config_changed`](https://documentation.ubuntu.com/juju/3.6/reference/hook/#config-changed): usually fired in response to a configuration change using the GUI or CLI. Create and set default cluster and cluster-set names in the peer relation databag (on the leader only).
6. [`update-status`](https://documentation.ubuntu.com/juju/3.6/reference/hook/#update-status): Takes care of workload health checks.
<!--- 7. database_storage_detaching: TODO: ops? event?
1. TODO: any other events?
--->

### Charm code overview

[`src/charm.py`](https://github.com/canonical/postgresql-operator/blob/main/src/charm.py) is the default entry point for a charm and has the `PostgresqlOperatorCharm` Python class which inherits from `CharmBase`.

`CharmBase` is the base class from which all Charms are formed, defined by [Ops](https://ops.readthedocs.io/en/latest/) (Python framework for developing charms). See more information in the [Ops documentation for `CharmBase`](https://ops.readthedocs.io/en/latest/reference/ops.html#ops.CharmBase).

The `__init__` method guarantees that the charm observes all events relevant to its operation and handles them.

The VM and K8s charm flavours shares the codebase via charm libraries in [`lib/charms/postgresql_k8s/v0/`](https://github.com/canonical/postgresql-k8s-operator/blob/main/lib/charms/postgresql_k8s/v0/postgresql.py) (of K8s flavour of the charm!):

```shell
$ charmcraft list-lib postgresql-k8s                                                                                                                                                                                                               

Library name    API    Patch                                                                                                                                                                                                                          
postgresql      0      12                                                                                                                                                                                                                             
postgresql_tls  0      7                                  
```

