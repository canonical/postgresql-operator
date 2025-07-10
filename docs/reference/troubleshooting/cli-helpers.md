# CLI helpers

This page describes some useful low-level tools shipped withing Charmed PostgreSQL for advanced troubleshooting.

```{caution}
**These tools can be dangerous in a production environment if they are not used correctly.**

When in doubt, [contact us](/reference/contacts).
```

## Patroni

Troubleshooting tools include:
* The command-line tool `patronictl`
* The Patroni REST API
* The Raft library 

Learn more about Patroni in the [Architecture](/explanation/architecture) page.

### `patronictl`

The main Patroni tool is `patronictl`. 

**It should only be used under the snap context**, via the user `_daemon_`.

#### Cluster status

`patronictl` checks the low-level Patroni status of the cluster.

<details><summary>Example: cluster status</summary>

```text
> juju deploy postgresql --channel 16/stable -n 3 # and wait for deployment
> juju ssh postgresql/2
...

ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml topology
+ Cluster: postgresql (7496847632512033809) ------+-----------+----+-----------+
| Member          | Host           | Role         | State     | TL | Lag in MB |
+-----------------+----------------+--------------+-----------+----+-----------+
| postgresql-1    | 10.189.210.201 | Leader       | running   |  1 |           |
| + postgresql-2  | 10.189.210.55  | Sync Standby | streaming |  1 |         0 |
| + postgresql-3  | 10.189.210.26  | Sync Standby | streaming |  1 |         0 |
+-----------------+----------------+--------------+-----------+----+-----------+
```
</details>
</br>

#### Useful Patroni actions

Use `--help` to find all the available Patroni actions.

<details><summary>Example: Patroni actions</summary>

```text
>  sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml --help
...
  failover     Failover to a replica
  history      Show the history of failovers/switchovers
  list         List the Patroni members for a given Patroni
  pause        Disable auto failover
  query        Query a Patroni PostgreSQL member
  reinit       Reinitialize cluster member
  reload       Reload cluster member configuration
  remove       Remove cluster from DCS
  restart      Restart cluster member
  resume       Resume auto failover
  switchover   Switchover to a replica
  topology     Prints ASCII topology for given cluster
```
</details>
</br>

#### Switchover/failover 

Patroni can perform a low-level [switchover/failover](https://patroni.readthedocs.io/en/latest/patronictl.html#patronictl-switchover) inside one cluster.

<details><summary>Example: switchover (healthy cluster only)</summary>

```text
ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml switchover postgresql --candidate postgresql-2 --force
Current cluster topology
+ Cluster: postgresql (7496847632512033809) ----+-----------+----+-----------+
| Member        | Host           | Role         | State     | TL | Lag in MB |
+---------------+----------------+--------------+-----------+----+-----------+
| postgresql-1  | 10.189.210.201 | Sync Standby | streaming |  2 |         0 |
| postgresql-2  | 10.189.210.55  | Sync Standby | streaming |  2 |         0 |
| postgresql-3  | 10.189.210.26  | Leader       | running   |  2 |           |
+---------------+----------------+--------------+-----------+----+-----------+
2025-04-25 04:59:10.87214 Successfully switched over to "postgresql-2"
+ Cluster: postgresql (7496847632512033809) -----------+----+-----------+
| Member        | Host           | Role    | State     | TL | Lag in MB |
+---------------+----------------+---------+-----------+----+-----------+
| postgresql-1  | 10.189.210.201 | Replica | running   |  2 |         0 |
| postgresql-2  | 10.189.210.55  | Leader  | running   |  2 |           |
| postgresql-3  | 10.189.210.26  | Replica | stopped   |    |   unknown |
+---------------+----------------+---------+-----------+----+-----------+

ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list
+ Cluster: postgresql (7496847632512033809) ----+-----------+----+-----------+
| Member        | Host           | Role         | State     | TL | Lag in MB |
+---------------+----------------+--------------+-----------+----+-----------+
| postgresql-1  | 10.189.210.201 | Sync Standby | streaming |  3 |         0 |
| postgresql-2  | 10.189.210.55  | Leader       | running   |  3 |           |
| postgresql-3  | 10.189.210.26  | Sync Standby | streaming |  3 |         0 |
+---------------+----------------+--------------+-----------+----+-----------+
```
</details>

<details><summary>Example: failover</summary>

```text
ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml failover postgresql --candidate postgresql-3              
Current cluster list
+ Cluster: postgresql (7496847632512033809) ----+-----------+----+-----------+
| Member        | Host           | Role         | State     | TL | Lag in MB |
+---------------+----------------+--------------+-----------+----+-----------+
| postgresql-1  | 10.189.210.201 | Leader       | running   |  1 |           |
| postgresql-2  | 10.189.210.55  | Sync Standby | streaming |  1 |         0 |
| postgresql-3  | 10.189.210.26  | Sync Standby | streaming |  1 |         0 |
+---------------+----------------+--------------+-----------+----+-----------+
Are you sure you want to failover cluster postgresql, demoting current leader postgresql-1? [y/N]: y
2025-04-25 04:44:53.69748 Successfully failed over to "postgresql-3"
+ Cluster: postgresql (7496847632512033809) ---------+----+-----------+
| Member        | Host           | Role    | State   | TL | Lag in MB |
+---------------+----------------+---------+---------+----+-----------+
| postgresql-1  | 10.189.210.201 | Replica | stopped |    |   unknown |
| postgresql-2  | 10.189.210.55  | Replica | running |  1 |         0 |
| postgresql-3  | 10.189.210.26  | Leader  | running |  1 |           |
+---------------+----------------+---------+---------+----+-----------+

ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml history
+----+-----------+------------------------------+----------------------------------+--------------+
| TL |       LSN | Reason                       | Timestamp                        | New Leader   |
+----+-----------+------------------------------+----------------------------------+--------------+
|  1 | 335544480 | no recovery target specified | 2025-04-25T04:44:53.137152+00:00 | postgresql-3 |
+----+-----------+------------------------------+----------------------------------+--------------+

ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list
+ Cluster: postgresql (7496847632512033809) ----+-----------+----+-----------+
| Member        | Host           | Role         | State     | TL | Lag in MB |
+---------------+----------------+--------------+-----------+----+-----------+
| postgresql-1  | 10.189.210.201 | Sync Standby | streaming |  2 |         0 |
| postgresql-2  | 10.189.210.55  | Sync Standby | streaming |  2 |         0 |
| postgresql-3  | 10.189.210.26  | Leader       | running   |  2 |           |
+---------------+----------------+--------------+-----------+----+-----------+
```
</details>
</br>

#### Re-initialisation

Sometimes the cluster member might stuck in the middle of nowhere, the easiest way to try is [`reinit` the Patroni cluster member](https://patroni.readthedocs.io/en/latest/patronictl.html#patronictl-reinit).
<details><summary>Example: cluster member re-initialisation</summary>

```text
ubuntu@juju-b87344-2:~$ sudo -u _daemon_ patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml reinit postgresql postgresql-1
+ Cluster: postgresql (7496847632512033809) ----+-----------+----+-----------+
| Member        | Host           | Role         | State     | TL | Lag in MB |
+---------------+----------------+--------------+-----------+----+-----------+
| postgresql-1  | 10.189.210.201 | Sync Standby | streaming |  3 |         0 |
| postgresql-2  | 10.189.210.55  | Leader       | running   |  3 |           |
| postgresql-3  | 10.189.210.26  | Sync Standby | streaming |  3 |         0 |
+---------------+----------------+--------------+-----------+----+-----------+
Are you sure you want to reinitialize members postgresql-1? [y/N]: y
Success: reinitialize for member postgresql-1
```

</details>
</br>

### Patroni REST API

Patroni provides most `patronictl` actions as a [REST API](https://patroni.readthedocs.io/en/latest/rest_api.html). Use port `8008` to access Patroni REST API on any member/unit of Charmed PostgreSQL.

<details><summary>Example: read-only access via Patroni REST API</summary>

```text
ubuntu@juju360:~$ curl 10.189.210.55:8008/cluster | jq # where 10.189.210.55 is IP of Charmed PostgreSQL Juju unit
...
{
  "members": [
    {
      "name": "postgresql-1",
      "role": "sync_standby",
      "state": "streaming",
      "api_url": "http://10.189.210.201:8008/patroni",
      "host": "10.189.210.201",
      "port": 5432,
      "timeline": 3,
      "lag": 0
    },
    {
      "name": "postgresql-2",
      "role": "leader",
      "state": "running",
      "api_url": "http://10.189.210.55:8008/patroni",
      "host": "10.189.210.55",
      "port": 5432,
      "timeline": 3
    },
    {
      "name": "postgresql-3",
      "role": "sync_standby",
      "state": "streaming",
      "api_url": "http://10.189.210.26:8008/patroni",
      "host": "10.189.210.26",
      "port": 5432,
      "timeline": 3,
      "lag": 0
    }
  ],
  "scope": "postgresql"
}                                                                                                                                  
```
</details>

```{note}
The Patroni REST API can be accessed anonymously in read-only mode only. The Juju secret `patroni-password` is mandatory to apply any chances via Patroni REST API.
```

Example of authenticated changes via Patroni REST API:
<details><summary>Example: write access via Patroni REST API</summary>

```text
> juju deploy postgresql --channel 16/stable -n 3 # and wait for deployment
> juju secrets | grep postgresql # find ID with 'patroni-password'
> juju show-secret --reveal ccccaaabbbbbbcgoi12345 | grep patroni-password # reveal password to access Patroni REST API
    patroni-password: patr0n1sup3rs3cretpassw0rd
...
> curl -k -u patroni:patr0n1sup3rs3cretpassw0rd -X POST https://10.151.27.242:8008/switchover -d '{"leader": "postgresql-0", "candidate": "postgresql-1"}'
Successfully switched over to "postgresql-1"
``` 
> **Hint**: use dedicated [promote-to-primary](/how-to/switchover-failover) action to switchover Primary.

</details>

Pay attention to TLS relation with PostgreSQL and access Patroni REST API accordingly:
<details><summary>Example: access Patroni REST API with(out) TLS</summary>

```text
> curl    http://x.x.x.x:8008/cluster  # to access without TLS
> curl    https://x.x.x.x:8008/cluster # to access with trusted certificate
> curl -k https://x.x.x.x:8008/cluster # to access with self-signed certificate
``` 
</details>
</br>

### Raft library

Patroni relies on the Raft library for the consensus handling and Primary election. It is implemented using [`pySyncObj`](https://github.com/bakwc/PySyncObj) and available as a CLI tool. 

While **you should not interact with Raft library manually**,  you can check its internal status. Note that a password is mandatory to access Raft.

<details><summary>Example: check Raft status</summary>

```text
> juju deploy postgresql --channel 16/stable -n 3 # and wait for deployment
> juju secrets | grep postgresql # find ID with 'raft-password'
> juju show-secret --reveal cvia1ibjihbbbcgoi12300 | grep raft-password # reveal password to access Raft
    raft-password: r@ftsup3rs3cretp@ssw0rd
> juju ssh postgresql/2
...
ubuntu@juju-b87344-2:~$ charmed-postgresql.syncobj-admin -status -conn 10.151.27.242:2222 -pass r@ftsup3rs3cretp@ssw0rd # where IP is a PostgreSQL Juju unit IP
commit_idx: 1396297
enabled_code_version: 0
has_quorum: True    <<<<<<<<<<<< Important Raft cluster health!
last_applied: 1396297
leader: 10.151.27.205:2222    <<<<<<<<<<<< Shows you the Raft leader
leader_commit_idx: 1396297
log_len: 69
match_idx_count: 2
match_idx_server_10.151.27.103:2222: 1310986
match_idx_server_10.151.27.205:2222: 1310986
next_node_idx_count: 2
next_node_idx_server_10.151.27.103:2222: 1310987
next_node_idx_server_10.151.27.205:2222: 1310987
partner_node_status_server_10.151.27.103:2222: 2
partner_node_status_server_10.151.27.205:2222: 2
partner_nodes_count: 2
raft_term: 92
readonly_nodes_count: 0    <<<<<<<<<<<< Raft members health
revision: deprecated
self: 10.151.27.242:2222
self_code_version: 0
state: 0    <<<<<<<<<<<< Self health
uptime: 2482881
version: 0.3.12
```

```{tip}
Pay attention to the CLI syntax. Use the standard hyphen `-`, avoid typos with the common `--` prefix for parameters.
```

</details>

