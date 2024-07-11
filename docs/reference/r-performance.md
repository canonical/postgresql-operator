# Performance and resource allocation

This page covers topics related to measuring and configuring the performance of PostgreSQL

---

<a href="#heading--performance"><h2 id="heading--performance"> Performance testing </h2></a>
For performance testing and benchmarking charms, we recommend using the [Charmed Sysbench](https://charmhub.io/sysbench) operator. This is a tool for benchmarking database applications that includes monitoring and CPU/RAM/IO performance measurement.

<a href="#heading--resource"><h2 id="heading--resource"> Resource allocation </h2></a>
Charmed PostgreSQL resource allocation can be controlled via the charm's `profile` config option. There are two profiles: `production` and `testing`. 

|Value|Description|Details|
| --- | --- | ----- |
|`production`<br>(default)|[Maximum performance](https://github.com/canonical/postgresql-operator/blob/main/lib/charms/postgresql_k8s/v0/postgresql.py#L437-L446)| 25% of the available memory for `shared_buffers` and the remain as cache memory (defaults mimic legacy charm behaviour).<br/>The `max_connections`=max(4 * os.cpu_count(), 100).<br/> Use [pgbouncer](https://charmhub.io/pgbouncer?channel=1/stable) if max_connections are not enough ([reasoning](https://www.percona.com/blog/scaling-postgresql-with-pgbouncer-you-may-need-a-connection-pooler-sooner-than-you-expect/)).|
|`testing`|[Minimal resource usage](https://github.com/canonical/postgresql-operator/blob/main/lib/charms/postgresql_k8s/v0/postgresql.py#L437-L446)|  PostgreSQL 14 defaults. |

[note type="caution"]
**Note**: Pre-deployed application profile change is planned but currently is NOT supported.
[/note]

You can set the profile during deployment using the `--config` flag. For example:
```shell
juju deploy postgresql --config profile=testing
```
You can change the profile using the `juju config` action. For example:
```shell
juju config postgresql profile=production
```
For a list of all of this charm's config options, see the [Configuration tab](https://charmhub.io/postgresql/configure#profile).

### Juju constraints

The Juju [`--constraints`](https://juju.is/docs/juju/constraint) flag sets RAM and CPU limits for [Juju units](https://juju.is/docs/juju/unit):

```shell
juju deploy postgresql --constraints cores=8 mem=16G
```

Juju constraints can be set together with the charm's profile:

```shell
juju deploy postgresql --constraints cores=8 mem=16G --config profile=testing
```