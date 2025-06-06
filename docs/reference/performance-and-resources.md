# Performance and resource allocation

This page covers topics related to measuring and configuring the performance of PostgreSQL

## Performance testing

For performance testing and benchmarking charms, we recommend using the [Charmed Sysbench](https://charmhub.io/sysbench) operator. This is a tool for benchmarking database applications that includes monitoring and CPU/RAM/IO performance measurement.

## Resource allocation

Charmed PostgreSQL resource allocation can be controlled via the charm's `profile` config option.

|Value|Description|Details|
| --- | --- | ----- |
|`production`<br>(default)|[Maximum performance](https://github.com/canonical/postgresql-operator/blob/main/lib/charms/postgresql_k8s/v0/postgresql.py#L437-L446)| 25% of the available memory for `shared_buffers` and the remain as cache memory (defaults mimic legacy charm behaviour).<br/>The `max_connections`=max(4 * os.cpu_count(), 100).<br/> Use [pgbouncer](https://charmhub.io/pgbouncer?channel=1/stable) if max_connections are not enough ([reasoning](https://www.percona.com/blog/scaling-postgresql-with-pgbouncer-you-may-need-a-connection-pooler-sooner-than-you-expect/)).|
|`testing`|[Minimal resource usage](https://github.com/canonical/postgresql-operator/blob/main/lib/charms/postgresql_k8s/v0/postgresql.py#L437-L446)|  PostgreSQL 14 defaults. |

```{caution}
Pre-deployed application profile change is planned but currently is NOT supported.
```

You can set the profile during deployment using the `--config` flag. For example:

```text
juju deploy postgresql --channel 14/stable --config profile=testing
```

You can change the profile using the `juju config` action. For example:

```text
juju config postgresql profile=production
```

For a list of all of this charm's config options, see the [Configuration tab](https://charmhub.io/postgresql/configure#profile).

### Juju constraints

The Juju [`--constraints`](https://juju.is/docs/juju/constraint) flag sets RAM and CPU limits for [Juju units](https://juju.is/docs/juju/unit):

```text
juju deploy postgresql --channel 14/stable --constraints cores=8 mem=16G
```

Juju constraints can be set together with the charm's profile:

```text
juju deploy postgresql --channel 14/stable --constraints cores=8 mem=16G --config profile=testing
```

