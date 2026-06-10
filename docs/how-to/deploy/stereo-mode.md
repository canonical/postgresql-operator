(deploy-stereo-mode)=
# How to deploy in stereo mode (two-node HA)

This guide shows how to deploy Charmed PostgreSQL in **stereo mode**: two `postgresql` units for the database, plus a separate `postgresql-watcher` application that supplies a third Raft vote. This gives you high availability with only two database copies.

For background on why the third vote is needed, see {ref}`stereo-mode`.

```{note}
Stereo mode requires `postgresql` revision 1149 or higher. The deploy commands below use the `16/edge` channel, which provides it.
```

## Prerequisites

* A bootstrapped Juju controller and a model. See the {ref}`deploy-quickstart`.
* For high availability that survives a zone outage, a cloud that provides [availability zones](https://en.wikipedia.org/wiki/Availability_zone). See {ref}`multi-az`.

## Deploy the database

Deploy `postgresql` with two units:

```shell
juju deploy postgresql --channel 16/edge -n 2
```

## Deploy the watcher

Deploy the standalone `postgresql-watcher` charm as a separate application, here named `pg-watcher`:

```{note}
With `--config profile=production` (the default), the watcher **blocks** if it shares an availability zone with a `postgresql` unit. For a single-AZ or local test environment, add `--config profile=testing` to downgrade the AZ check to a warning.
```

```shell
juju deploy postgresql-watcher pg-watcher --channel 16/edge
```

## Integrate the watcher with PostgreSQL

Relate the two applications over the watcher endpoints:

```shell
juju integrate postgresql:watcher-offer pg-watcher:watcher
```

## Verify the deployment

Wait until all units settle to `active/idle`, then run `juju status`. You should see two active `postgresql` units (one marked `Primary`) and an active `pg-watcher`, each on its own machine and — on a cloud with multiple availability zones — in a distinct availability zone:

```text
Model    Controller  Cloud/Region     Version  SLA          Timestamp
mymodel  gce         google/us-east1  3.6.23   unsupported  09:42:31+02:00

App         Version  Status  Scale  Charm               Channel   Rev  Exposed  Message
pg-watcher  16.14    active      1  postgresql-watcher  16/edge    25  no
postgresql  16.14    active      2  postgresql          16/edge  1150  no

Unit           Workload  Agent  Machine  Public address  Ports     Message
pg-watcher/0*  active    idle   2        34.138.167.85             Raft connected, monitoring 2 PostgreSQL endpoints
postgresql/0*  active    idle   0        34.148.44.51    5432/tcp  Primary
postgresql/1   active    idle   1        34.23.202.220   5432/tcp

Machine  State    Address        Inst id        Base          AZ          Message
0        started  34.148.44.51   juju-e7c0db-0  ubuntu@24.04  us-east1-d  RUNNING
1        started  34.23.202.220  juju-e7c0db-1  ubuntu@24.04  us-east1-c  RUNNING
2        started  34.138.167.85  juju-e7c0db-2  ubuntu@24.04  us-east1-b  RUNNING
```

The `AZ` column confirms the two `postgresql` units and the watcher each sit in a different availability zone, as the production profile requires.

```{note}
If `pg-watcher` stays `blocked` reporting that it shares an availability zone with a `postgresql` unit, your units are not spread across AZs. Either spread them across multiple zones (see {ref}`multi-az`) or, for a test environment, switch the watcher to the testing profile with `juju config pg-watcher profile=testing`.
```

The cluster now has three Raft voters — two `postgresql` units plus the watcher — so it can elect a new primary automatically if one database unit is lost.
