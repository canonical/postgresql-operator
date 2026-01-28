(troubleshooting)=
# Troubleshooting

This page goes over some recommended tools and approaches to troubleshooting the charm.

Before anything, always run `juju status` to check the [list of charm statuses](/reference/statuses) and the recommended fixes. This alone may already solve your issue. 

Otherwise, this reference goes over how to troubleshoot this charm via:
- [Troubleshooting](#troubleshooting)
  - [Summary](#summary)
  - [Juju logs](#juju-logs)
  - [Snap-based charm](#snap-based-charm)
  - [Install extra software](#install-extra-software)


```{caution}
At the moment, there is no support for [pausing an operator](https://warthogs.atlassian.net/browse/DPE-2545).

Make sure your activity will not interfere with the operator itself!
```

## Juju logs

See:
* [Juju | logs](https://juju.is/docs/juju/log)
* [Juju | How to manage logs](https://juju.is/docs/juju/manage-logs).

Always check the Juju logs before troubleshooting further:

```text
juju debug-log --replay --tail
```

Focus on `ERRORS` (normally there should be none):

```text
juju debug-log --replay | grep -c ERROR
```

Consider enabling the `DEBUG` log level if you are troubleshooting unusual charm behaviour:

```text
juju model-config 'logging-config=<root>=INFO;unit=DEBUG'
```

The Patroni/PostgreSQL logs are located inside the snap:

```text
> ls -la /var/snap/charmed-postgresql/common/var/log/*

/var/snap/charmed-postgresql/common/var/log/patroni:
-rw-r--r-- 1 _daemon_ _daemon_ 292519 Sep 15 21:47 patroni.log

/var/snap/charmed-postgresql/common/var/log/pgbackrest:
-rw-r----- 1 _daemon_ _daemon_ 7337 Sep 15 21:46 all-server.log
-rw-r----- 1 _daemon_ _daemon_ 5858 Sep 15 10:41 testbet.postgresql-stanza-create.log

/var/snap/charmed-postgresql/common/var/log/pgbouncer:
# The pgBouncer should be stopped on Charmed PostgreSQL deployments and produce no logs.
```

## Snap-based charm

First, check the [operator architecture](/explanation/architecture) to become familiar with snap content, operator building blocks, and running Juju units.

To enter the unit, use:

```text
juju ssh postgresql/0 bash
```

Make sure the `charmed-postgresql` snap is installed and functional:

```text
ubuntu@juju-fd7874-0:~$ sudo snap list charmed-postgresql
Name                Version  Rev  Tracking       Publisher        Notes
charmed-postgresql  14.9     70   latest/stable  dataplatformbot  held
```

From here you can make sure all snap (`systemd`) services are running:

```text
ubuntu@juju-fd7874-0# sudo snap services
Service                                          Startup   Current   Notes
charmed-postgresql.patroni                       enabled   active    -
charmed-postgresql.pgbackrest-service            enabled   active    -
charmed-postgresql.prometheus-postgres-exporter  enabled   active    -
charmed-postgresql.pgbackrest-exporter           enabled   active    -

ubuntu@juju-fd7874-0:~$ systemctl --failed
...
0 loaded units listed.

ubuntu@juju-fd7874-0:~$ ps auxww
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.4  0.0 167364 12716 ?        Ss   21:40   0:02 /sbin/init
root          59  0.0  0.0  64596 20828 ?        Ss   21:40   0:00 /lib/systemd/systemd-journald
root         112  0.0  0.0  11088  5740 ?        Ss   21:40   0:00 /lib/systemd/systemd-udevd
root         115  0.3  0.0   4832  1816 ?        Ss   21:40   0:01 snapfuse /var/lib/snapd/snaps/core22_864.snap /snap/core22/864 -o ro,nodev,allow_other,suid
root         116  0.2  0.0   4896  1880 ?        Ss   21:40   0:01 snapfuse /var/lib/snapd/snaps/charmed-postgresql_70.snap /snap/charmed-postgresql/70 -o ro,nodev,allow_other,suid
root         117  0.0  0.0   4748  1644 ?        Ss   21:40   0:00 snapfuse /var/lib/snapd/snaps/core20_2015.snap /snap/core20/2015 -o ro,nodev,allow_other,suid
root         119  0.0  0.0   4692  1600 ?        Ss   21:40   0:00 snapfuse /var/lib/snapd/snaps/lxd_24322.snap /snap/lxd/24322 -o ro,nodev,allow_other,suid
root         120  0.6  0.0   4768  1840 ?        Ss   21:40   0:04 snapfuse /var/lib/snapd/snaps/snapd_19993.snap /snap/snapd/19993 -o ro,nodev,allow_other,suid
systemd+     225  0.0  0.0  16116  8100 ?        Ss   21:40   0:00 /lib/systemd/systemd-networkd
systemd+     227  0.0  0.0  25528 12664 ?        Ss   21:40   0:00 /lib/systemd/systemd-resolved
root         241  0.0  0.0   7284  2792 ?        Ss   21:40   0:00 /usr/sbin/cron -f -P
message+     243  0.0  0.0   8668  4916 ?        Ss   21:40   0:00 @dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation --syslog-only
root         247  0.0  0.0  33084 18792 ?        Ss   21:40   0:00 /usr/bin/python3 /usr/bin/networkd-dispatcher --run-startup-triggers
syslog       248  0.0  0.0 152764  4748 ?        Ssl  21:40   0:00 /usr/sbin/rsyslogd -n -iNONE
snap_da+     250  0.0  0.0 1303900 10216 ?       Ssl  21:40   0:00 /snap/charmed-postgresql/70/usr/bin/prometheus-postgres-exporter
root         254  0.0  0.0  15312  7456 ?        Ss   21:40   0:00 /lib/systemd/systemd-logind
root         281  0.0  0.0   7760  3508 ?        Ss   21:40   0:00 bash /etc/systemd/system/jujud-machine-0-exec-start.sh
root         294  0.0  0.0   6216  1064 pts/0    Ss+  21:40   0:00 /sbin/agetty -o -p -- \u --noclear --keep-baud console 115200,38400,9600 vt220
root         296  0.0  0.0  15420  9240 ?        Ss   21:40   0:00 sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups
root         301  2.2  0.2 895540 97552 ?        Sl   21:40   0:13 /var/lib/juju/tools/machine-0/jujud machine --data-dir /var/lib/juju --machine-id 0 --debug
root         335  0.0  0.0 110084 21336 ?        Ssl  21:40   0:00 /usr/bin/python3 /usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal
root         418  0.0  0.0 235452  8128 ?        Ssl  21:40   0:00 /usr/libexec/polkitd --no-debug
root         772  0.4  0.0   4764  1780 ?        Ss   21:40   0:02 snapfuse /var/lib/snapd/snaps/snapd_20092.snap /snap/snapd/20092 -o ro,nodev,allow_other,suid
root         850  0.2  0.1 2058980 33536 ?       Ssl  21:40   0:01 /usr/lib/snapd/snapd
root        1587  0.0  0.0   4780  3264 ?        Ss   21:40   0:00 /bin/bash /snap/charmed-postgresql/70/start-patroni.sh
snap_da+    1615  1.1  0.1 490500 39308 ?        Sl   21:40   0:06 python3 /snap/charmed-postgresql/70/usr/bin/patroni /var/snap/charmed-postgresql/70/etc/patroni/patroni.yaml
snap_da+    2582  0.0  0.0 215816 30076 ?        S    21:41   0:00 /snap/charmed-postgresql/current/usr/lib/postgresql/14/bin/postgres -D /var/snap/charmed-postgresql/common/var/lib/postgresql --config-file=/var/snap/charmed-postgresql/common/var/lib/postgresql/postgresql.conf --listen_addresses=10.47.228.200 --port=5432 --cluster_name=postgresql --wal_level=logical --hot_standby=on --max_connections=100 --max_wal_senders=10 --max_prepared_transactions=0 --max_locks_per_transaction=64 --track_commit_timestamp=off --max_replication_slots=10 --max_worker_processes=8 --wal_log_hints=on
snap_da+    2808  0.0  0.0 215816 10704 ?        Ss   21:41   0:00 postgres: postgresql: checkpointer 
snap_da+    2810  0.0  0.0 215816 10496 ?        Ss   21:41   0:00 postgres: postgresql: background writer 
snap_da+    2811  0.0  0.0  70540  8804 ?        Ss   21:41   0:00 postgres: postgresql: stats collector 
snap_da+    2840  0.0  0.0 217980 21184 ?        Ss   21:41   0:00 postgres: postgresql: operator postgres 10.47.228.200(36138) idle
snap_da+    2947  0.0  0.0 216716 14736 ?        Ss   21:41   0:00 postgres: postgresql: walsender replication 10.47.228.241(45254) streaming 0/A002FA8
snap_da+    2952  0.0  0.0 215816 13140 ?        Ss   21:41   0:00 postgres: postgresql: walwriter 
snap_da+    2953  0.0  0.0 216424 10848 ?        Ss   21:41   0:00 postgres: postgresql: autovacuum launcher 
snap_da+    2954  0.0  0.0 215816  9132 ?        Ss   21:41   0:00 postgres: postgresql: archiver last was 00000001000000000000000A.partial
snap_da+    2955  0.0  0.0 216260  9516 ?        Ss   21:41   0:00 postgres: postgresql: logical replication launcher 
snap_da+    6556  0.0  0.0 216780 14780 ?        Ss   21:42   0:00 postgres: postgresql: walsender replication 10.47.228.164(48482) streaming 0/A002FA8
root        6799  0.0  0.0  39900 31164 ?        S    21:42   0:00 /usr/bin/python3 src/cluster_topology_observer.py https://10.47.228.200:8008 /var/snap/charmed-postgresql/current/etc/patroni/ca.pem /usr/bin/juju-run postgresql/0 /var/lib/juju/agents/unit-postgresql-0/charm
root        9831  0.0  0.0   4780  3204 ?        Ss   21:46   0:00 /bin/bash /snap/charmed-postgresql/70/start-pgbackrest.sh
snap_da+    9859  0.0  0.0  56152 13584 ?        S    21:46   0:00 /snap/charmed-postgresql/70/usr/bin/pgbackrest server --config=/var/snap/charmed-postgresql/70/etc/pgbackrest/pgbackrest.conf
root       10168  0.0  0.0  16908 10836 ?        Ss   21:47   0:00 sshd: ubuntu [priv]
ubuntu     10171  0.0  0.0  17056  9628 ?        Ss   21:47   0:00 /lib/systemd/systemd --user
ubuntu     10172  0.0  0.0 170148  4728 ?        S    21:47   0:00 (sd-pam)
ubuntu     10234  0.0  0.0  17208  7944 ?        R    21:47   0:00 sshd: ubuntu@pts/1
ubuntu@juju-fd7874-0:~$
```

The list of running snap/`systemd` services will depend on configured (enabled) [COS integration](/how-to/monitoring-cos/enable-monitoring) and/or [backup](/how-to/back-up-and-restore/create-a-backup) functionality. The snap service `charmed-postgresql.patroni` must always be active and currently running (the Linux processes `snapd`, `patroni` and `postgres`).

Access PostgreSQL with the `psql` CLI tool and continue troubleshooting your database-related issues from here. 

```shell
juju show-unit postgresql/0 | awk '/private-address:/{print $2;exit}'

juju secrets # to find secret ID

juju show-secret --reveal <secret ID> | grep operator
```

```{seealso}
* {ref}`users`
* {ref}`manage-passwords`
```

```{caution}
To avoid split-brain scenarios:

* Do not manage users, credentials, databases, and schema directly. 
* Avoid restarting services directly. If you see the problem with a unit, consider [removing the failing unit and adding a new unit](/how-to/scale-replicas) to recover the cluster state.
```

As a last resort, [contact us](/reference/contacts) if you cannot determine the source of your issue.

Also, feel free to improve this document!

## Install extra software

We recommend you do **not** install any additional software. This may affect stability and produce anomalies that are hard to troubleshoot.

Sometimes, however, it is necessary to install some extra troubleshooting software. 

Use the common approach:

```text
ubuntu@juju-fd7874-0:~$ sudo apt update && sudo apt install gdb
...
Setting up gdb (12.1-0ubuntu1~22.04) ...
ubuntu@juju-fd7874-0:~$
```

```{tip}
Always remove manually installed components at the end of troubleshooting. Keep the house clean!
```

```{toctree}
:titlesonly:
:maxdepth: 2
:hidden:

SOS report <sos-report>
CLI helpers <cli-helpers>
```