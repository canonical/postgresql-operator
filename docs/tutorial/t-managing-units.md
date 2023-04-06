# Scale your Charmed PostgreSQL

This is part of the [Charmed PostgreSQL Tutorial](TODO). Please refer to this page for more information and the overview of the content.

## Adding and Removing units

Charmed PostgreSQL operator uses [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, automatic failover, and so on. The charm uses Postgres’s [Synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-synchronous-replication) with Patroni.

> **!** *Disclaimer: this tutorial hosts replicas all on the same machine, this should not be done in a production environment. To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).*


### Add cluster members (replicas)
You can add two replicas to your deployed PostgreSQL application with:
```shell
juju add-unit postgresql -n 2
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:16:44+01:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql           active      3  postgresql  edge     281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           Primary
postgresql/1   active    idle   1        10.89.49.197           
postgresql/2   active    idle   2        10.89.49.175           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
2        started  10.89.49.175  juju-a8a31d-2  jammy       Running
```

### Remove cluster members (replicas)
Removing a unit from the application, scales the replicas down. Before we scale down the replicas, list all the units with `juju status`, here you will see three units `postgresql/0`, `postgresql/1`, and `postgresql/2`. Each of these units hosts a PostgreSQL replica. To remove the replica hosted on the unit `postgresql/2` enter:
```shell
juju remove-unit postgresql/2
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:17:14+01:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql           active      2  postgresql  edge     281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           
postgresql/1   active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
```