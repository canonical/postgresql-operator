# Switchover / failover

Charmed PostgreSQL constantly monitors the cluster status and performs **automated failover** in case of Primary unit gone. Sometimes **manual switchover** is necessary for hardware maintenance reasons. Check the difference between them [here](https://dbvisit.com/blog/difference-between-failover-vs-switchover).

[Manual switchover](https://en.wikipedia.org/wiki/Switchover) is possible using Juju action [promote-to-primary](https://charmhub.io/postgresql/actions#promote-to-primary). 

Charmed PostgreSQL has been designed for maximum guarantee of data survival in all corner cases. As such, allowed actions depend on the configured [Juju unit state](/explanation/units).

## Switchover 

To switchover the PostgreSQL Primary (write-endpoint) to new Juju unit, use Juju action `promote-to-primary` (on the unit `x`, which will be promoted as a new primary):

```text
juju run postgresql/x promote-to-primary scope=unit
```

Note that:
* a manual switchover is possible on the healthy '[Sync Standby](/explanation/units)' unit only. Otherwise it will be rejected by Patroni with the reason explanation.
* the [Juju leader](https://documentation.ubuntu.com/juju/3.6/reference/unit/#leader-unit) unit and PostgreSQL primary unit are normally pointing to different [Juju units](https://documentation.ubuntu.com/juju/3.6/reference/unit/). Juju leader failover is fully automated and can be [enforced](https://github.com/canonical/jhack?tab=readme-ov-file#elect) for educational purpose only! Do **not** trigger Juju leader election to move the primary.

## Failover

Charmed PostgreSQL doesn't provide manual failover due to lack of data safety guarantees.

Advanced users can still execute it using [patronictl](/reference/troubleshooting/cli-helpers) and [Patroni REST API](/reference/troubleshooting/cli-helpers). The same time Charmed PostgreSQL allows the cluster recovery using the full PostgreSQL/Patroni/Raft cluster re-initialisation.

## Raft re-initialisation

```{caution}
This is the worst possible recovery case scenario when Primary and ALL Sync Standby units lost simultaneously and their data cannot be recovered from the disc. 

In this case, Patroni cannot perform automatic failover for the only available Replica(s) units. Still Patroni provides the read-only access to the data.

A manual failover procedure cannot guarantee the latest SQL transactions' availability on the replica unit(s) due to the [lag distance](/explanation/units) to the primary. Additionally, Raft cluster consensus is not possible when one unit is left in a three-unit cluster. 
```

The command to re-initialise the Raft cluster should be executed when charm is ready:
* the last Juju unit is available in Juju application
* the last unit was has detected Raft majority lost, status: `Raft majority loss, run: promote-to-primary`

To re-initialise Raft and fix the Partition/PostgreSQL cluster (when requested):

```text
juju run postgresql/x promote-to-primary scope=unit force=true
```

<details><summary>Example of Raft re-initialisation</summary>

Deploy PostgreSQL 3 units:

```text
> juju deploy postgresql --channel 14/stable --config synchronous_node_count=1

> juju status 
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  lxd         localhost/localhost  3.6.5    unsupported  14:50:19+02:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql  14.17    active      3  postgresql  14/edge  615  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        10.189.210.53   5432/tcp  
postgresql/1   active    idle   1        10.189.210.166  5432/tcp  
postgresql/2   active    idle   2        10.189.210.188  5432/tcp  Primary

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.189.210.53   juju-422c1a-0  ubuntu@22.04      Running
1        started  10.189.210.166  juju-422c1a-1  ubuntu@22.04      Running
2        started  10.189.210.188  juju-422c1a-2  ubuntu@22.04      Running
```

Find the current primary/standby/replica:

```text
> juju ssh postgresql/0
ubuntu@juju-422c1a-0:~$ sudo -u snap_daemon patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list
+ Cluster: postgresql (7499430436963402504) ---+-----------+----+-----------+
| Member       | Host           | Role         | State     | TL | Lag in MB |
+--------------+----------------+--------------+-----------+----+-----------+
| postgresql-0 | 10.189.210.53  | Sync Standby | streaming |  3 |         0 |
| postgresql-1 | 10.189.210.166 | Replica      | streaming |  3 |         0 |
| postgresql-2 | 10.189.210.188 | Leader       | running   |  3 |           |
+--------------+----------------+--------------+-----------+----+-----------+
```

Kill the leader and sync standby machines:

```text
> lxc stop --force juju-422c1a-0  && lxc stop --force juju-422c1a-2

> juju status 
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  lxd         localhost/localhost  3.6.5    unsupported  14:54:40+02:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql  14.17    active    1/3  postgresql  14/edge  615  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0   unknown   lost   0        10.189.210.53   5432/tcp  agent lost, see 'juju show-status-log postgresql/0'
postgresql/1*  active    idle   1        10.189.210.166  5432/tcp  <<<<<<<<< Replica unit left only
postgresql/2   unknown   lost   2        10.189.210.188  5432/tcp  agent lost, see 'juju show-status-log postgresql/2'

Machine  State    Address         Inst id        Base          AZ  Message
0        down     10.189.210.53   juju-422c1a-0  ubuntu@22.04      Running
1        started  10.189.210.166  juju-422c1a-1  ubuntu@22.04      Running
2        down     10.189.210.188  juju-422c1a-2  ubuntu@22.04      Running
```

At this stage it is recommended to restore the lost nodes, they will rejoin the cluster automatically once Juju detects their availability.

To start Raft re-initialisation, remove DEAD machines as a signal to charm that they cannot be restored/started and no risks for split-brain:

```text
> juju remove-machine --force 0 
WARNING This command will perform the following actions:
will remove machine 0
- will remove unit postgresql/0
- will remove storage pgdata/0
Continue [y/N]? y

> juju remove-machine --force 2
WARNING This command will perform the following actions:
will remove machine 2
- will remove unit postgresql/2
- will remove storage pgdata/2
Continue [y/N]? y
```

Check the status to ensure `Raft majority loss`:

```text
> juju status
...
Unit           Workload  Agent      Machine  Public address  Ports     Message
postgresql/1*  blocked   executing  1        10.189.210.166  5432/tcp  Raft majority loss, run: promote-to-primary
...
```

Start Raft re-initialisation:

```text
> juju run postgresql/1 promote-to-primary scope=unit force=true
```

Wait for re-initiation to be completed:

```
> juju status
...
Unit           Workload     Agent      Machine  Public address  Ports     Message
postgresql/1*  maintenance  executing  3        10.189.210.166  5432/tcp  (promote-to-primary) Reinitialising raft
...
```

At the end, the primary until is back:

```text
> juju status
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  lxd         localhost/localhost  3.6.5    unsupported  15:03:12+02:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql  14.17    active      1  postgresql  14/edge  615  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/1*  active    idle   1        10.189.210.166  5432/tcp  Primary

Machine  State    Address         Inst id        Base          AZ  Message
1        started  10.189.210.166  juju-422c1a-1  ubuntu@22.04      Running
```

Scale application to 3+ units to complete HA recovery:

```text
> juju add-unit postgresql -n 2
```

The healthy status:
```text
> juju status
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  lxd         localhost/localhost  3.6.5    unsupported  15:09:56+02:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql  14.17    active      3  postgresql  14/edge  615  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/1*  active    idle   1        10.189.210.166  5432/tcp  Primary
postgresql/3   active    idle   3        10.189.210.124  5432/tcp  
postgresql/4   active    idle   4        10.189.210.178  5432/tcp  

Machine  State    Address         Inst id        Base          AZ  Message
1        started  10.189.210.166  juju-422c1a-1  ubuntu@22.04      Running
3        started  10.189.210.124  juju-422c1a-3  ubuntu@22.04      Running
4        started  10.189.210.178  juju-422c1a-4  ubuntu@22.04      Running
```
</details>

