# PostgreSQL units

Each [HA](https://en.wikipedia.org/wiki/High_availability)/[DR](https://en.wikipedia.org/wiki/IT_disaster_recovery) implementation has a primary and secondary (standby) site(s).
Charmed PostgreSQL cluster size can be [easily scaled](/t/11863) from 0 to 10 units ([contact us](/t/11863) for 10+ units cluster). It is recommended to use 3+ units cluster size in production (due to [Raft consensus](https://en.wikipedia.org/wiki/Raft_(algorithm)) requirements). Those units type can be:
  * **Primary**: unit which accepts all writes and [guaranties no split brain](https://en.wikipedia.org/wiki/Split-brain_(computing)).
  * **Sync Standby** (synchronous copy) : designed for the fast automatic failover. Used for read-only queries and guaranties the latest transaction availability.
  * **Replica** (asynchronous copy): designed for long-running and resource consuming queries without affecting Primary performance. Used for read-only queries without guaranties of the latest transaction availability.

> **Warning**: all SQL transactions have to be confirmed by all Sync Standby unit(s) before Primary unit commit transaction to the client. Therefor the high-performance and high-availability is a trade-of balance between "Sync Standby" and "Replica" units count in the cluster.

> **Note**: starting from revision 561 all Charmed PostgreSQL units are configured as Sync Standby members by default. It provides better guaranties for the data survival when two of three units gone simultaneously. Users can re-configure the necessary synchronous units count using Juju config option '[synchronous_node_count](https://charmhub.io/postgresql/configurations?channel=14/edge#synchronous_node_count)'.

![PostgreSQL Units types|690x253, 100%](upload://pY5kzxO9ELJGEqEe1F1RQjOG6SS.png)

## Primary

The simplest way to find the Primary unit is to run `juju status`. Please be aware that the information here can be outdated as it is being updated only on [Juju event 'update-status'](https://documentation.ubuntu.com/juju/3.6/reference/hook/#update-status): 
```shell
ubuntu@juju360:~$ juju status postgresql
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  lxd         localhost/localhost  3.6.5    unsupported  13:04:15+02:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql  14.15    active      3  postgresql  14/stable  553  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        10.189.210.53   5432/tcp  Primary <<<<<<<<<<<<<<
postgresql/1   active    idle   1        10.189.210.166  5432/tcp  
postgresql/2   active    idle   2        10.189.210.188  5432/tcp  

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.189.210.53   juju-422c1a-0  ubuntu@22.04      Running
1        started  10.189.210.166  juju-422c1a-1  ubuntu@22.04      Running
2        started  10.189.210.188  juju-422c1a-2  ubuntu@22.04      Running
```

The up-to-date Primary unit number can be received using Juju action `get-primary`:
```shell
> juju run postgresql/leader get-primary
...
primary: postgresql/0
```

Also it is possible to retrieve this information using [patronictl](/t/17406#p-37204-patronictl-3) and [Patroni REST API](/t/17406#p-37204-patroni-rest-api-8).

## Standby / Replica

At the moment it is possible to retrieve this information using [patronictl](/t/17406#p-37204-patronictl-3) and [Patroni REST API](/t/17406#p-37204-patroni-rest-api-8) only (check the linked documentation for the access details). Example:
```shell
> ... patronictl ... list
+ Cluster: postgresql (7499430436963402504) ---+-----------+----+-----------+
| Member       | Host           | Role         | State     | TL | Lag in MB |
+--------------+----------------+--------------+-----------+----+-----------+
| postgresql-0 | 10.189.210.53  | Leader       | running   |  1 |           |
| postgresql-1 | 10.189.210.166 | Sync Standby | streaming |  1 |         0 |
| postgresql-2 | 10.189.210.188 | Replica      | streaming |  1 |         0 |
+--------------+----------------+--------------+-----------+----+-----------+
```
On the example above:
* `postgresql-0` is a PostgreSQL Primary unit (Patroni Leader) which accepts all writes
* `postgresql-1` is a PostgreSQL/Patroni Sync Standby unit which can be promoted as new primary using manual switchover (safe).
* `postgresql-2` is a PostgreSQL/Patroni Replica unit which can NOT be directly promoted as a new Primary using manual switchover. The automatic promotion Replica=>Sync Standby is necessary to guaranties the latest SQL transactions availability on this unit to allow further promotion as a new Primary. Otherwise the manual failover can be performed to Replica unit accepting the risks of loosing the last transactions(s) which lagged behind Primary. 

## Replica lag distance

At the moment it is possible to retrieve this information using [patronictl](/t/17406#p-37204-patronictl-3) and [Patroni REST API](/t/17406#p-37204-patroni-rest-api-8) only (check the linked documentation for the access details). Example:
```shell
> ... patronictl ... list
+ Cluster: postgresql (7499430436963402504) ---+-----------+----+-----------+
| Member       | Host           | Role         | State     | TL | Lag in MB |
+--------------+----------------+--------------+-----------+----+-----------+
| postgresql-0 | 10.189.210.53  | Leader       | running   |  1 |           |
| ...
| postgresql-2 | 10.189.210.188 | Replica      | streaming |  1 |        42 |  <<<<<
+--------------+----------------+--------------+-----------+----+-----------+

> curl ... x.x.x.x:8008/cluster | jq
  "members": [
    {
      "name": "postgresql-0",
      "role": "leader",
      "state": "running",
      ...
    },
...
    {
      "name": "postgresql-2",
      "role": "replica",
      "state": "streaming",
      ...
      "lag": 42 <<<<<<<<<<<< Lag in MB
    }
```