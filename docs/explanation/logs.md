# Logs

The list of all the charm components are well described in the [](/explanation/architecture).

It is a dedicated section to highlight logs for each component to simplify troubleshooting.

## Core logs

PostgreSQL and Patroni logs can be found in `/var/snap/charmed-postgresql/common/var/log/postgresql` and `/var/snap/charmed-postgresql/common/var/log/patroni` respectively:

```text
> ls -alh /var/snap/charmed-postgresql/common/var/log/postgresql
total 20K
drwxr-xr-x 2 snap_daemon root        4.0K Oct 11 15:09 .
drwxr-xr-x 6 snap_daemon root        4.0K Oct 11 15:04 ..
-rw------- 1 snap_daemon snap_daemon 4.3K Oct 11 15:05 postgresql-3_1505.log
-rw------- 1 snap_daemon snap_daemon    0 Oct 11 15:06 postgresql-3_1506.log
-rw------- 1 snap_daemon snap_daemon    0 Oct 11 15:07 postgresql-3_1507.log
-rw------- 1 snap_daemon snap_daemon  817 Oct 11 15:08 postgresql-3_1508.log
-rw------- 1 snap_daemon snap_daemon    0 Oct 11 15:09 postgresql-3_1509.log
```

```text
>  ls -alh /var/snap/charmed-postgresql/common/var/log/patroni/
total 28K
drwxr-xr-x 2 snap_daemon root        4.0K Oct 11 15:29 .
drwxr-xr-x 6 snap_daemon root        4.0K Oct 11 15:25 ..
-rw-r--r-- 1 snap_daemon snap_daemon  356 Oct 11 15:29 patroni.log
-rw-r--r-- 1 snap_daemon snap_daemon  534 Oct 11 15:28 patroni.log.1
-rw-r--r-- 1 snap_daemon snap_daemon  520 Oct 11 15:27 patroni.log.2
-rw-r--r-- 1 snap_daemon snap_daemon  584 Oct 11 15:27 patroni.log.3
-rw-r--r-- 1 snap_daemon snap_daemon  464 Oct 11 15:27 patroni.log.4
```

The PostgreSQL log naming convention  is `postgresql-<weekday>_<hour><minute>.log`. The log message format is `<date> <time> UTC [<pid>]: <connection details> <level>: <message>`. E.g:

```text
> cat /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-3_1508.log
2023-10-11 15:08:17 GMT [4338]: user=,db=,app=,client=,line=8 LOG:  received SIGHUP, reloading configuration files
2023-10-11 15:08:17 GMT [4338]: user=,db=,app=,client=,line=9 LOG:  parameter "archive_command" changed to "pgbackrest --config=/var/snap/charmed-postgresql/current/etc/pgbackrest/pgbackrest.conf --stanza=pg.pg archive-push %p"
2023-10-11 15:08:21 GMT [9435]: user=backup,db=postgres,app=pgBackRest [check],client=[local],line=1 LOG:  restore point "pgBackRest Archive Check" created at 0/19A86D0
2023-10-11 15:08:21 GMT [9435]: user=backup,db=postgres,app=pgBackRest [check],client=[local],line=2 STATEMENT:  select pg_catalog.pg_create_restore_point('pgBackRest Archive Check')::text
2023-10-11 15:08:27 GMT [4338]: user=,db=,app=,client=,line=10 LOG:  received SIGHUP, reloading configuration files
```

The Patroni log message format is `<date> <time> UTC [<pid>]: <level>: <message>`. E.g:

```text
> cat /var/snap/charmed-postgresql/common/var/log/patroni/patroni.log.4
2023-10-11 15:27:01 UTC [4247]: WARNING: Could not activate Linux watchdog device: "Can't open watchdog device: [Errno 2] No such file or directory: '/dev/watchdog'" 
2023-10-11 15:27:01 UTC [4247]: INFO: initialized a new cluster 
2023-10-11 15:27:01 UTC [4247]: INFO: no action. I am (pg2-0), the leader with the lock 
2023-10-11 15:27:11 UTC [4247]: INFO: No local configuration items changed. 
2023-10-11 15:27:11 UTC [4247]: INFO: Changed archive_mode from on to True (restart might be required) 
2023-10-11 15:27:11 UTC [4247]: INFO: Changed synchronous_commit from on to True 
```

All timestamps are in UTC.

## Optional logs

If S3 backups are enabled, Pgbackrest logs would be located in `/var/snap/charmed-postgresql/common/var/log/pgbackrest`:

```text
> ls -alh  /var/snap/charmed-postgresql/common/var/log/pgbackrest/
total 20K
drwxr-xr-x 2 snap_daemon root        4.0K Oct 11 15:14 .
drwxr-xr-x 6 snap_daemon root        4.0K Oct 11 15:04 ..
-rw-r----- 1 snap_daemon snap_daemon 1.7K Oct 11 15:14 pg.pg-backup.log
-rw-r----- 1 snap_daemon snap_daemon  717 Oct 11 15:14 pg.pg-expire.log
-rw-r----- 1 snap_daemon snap_daemon  859 Oct 11 15:08 pg.pg-stanza-create.log
```

The naming convention of the Pgbackrest logs is `<model name>.patroni-<postgresql app name>-<action>.log`. Log output should look similar to:

```text
> cat /var/snap/charmed-postgresql/common/var/log/pgbackrest/pg.pg-expire.log 
-------------------PROCESS START-------------------
2023-10-11 15:14:44.555 P00   INFO: expire command begin 2.47: --config=/var/snap/charmed-postgresql/current/etc/pgbackrest/pgbackrest.conf --exec-id=11725-9ad622c8 --lock-path=/tmp --log-level-console=debug --log-path=/var/snap/charmed-postgresql/common/var/log/pgbackrest --repo1-path=/postgresql-test2 --repo1-retention-full=9999999 --repo1-s3-bucket=dragop-test-bucket --repo1-s3-endpoint=https://s3.eu-central-1.amazonaws.com --repo1-s3-key=<redacted> --repo1-s3-key-secret=<redacted> --repo1-s3-region=eu-central-1 --repo1-s3-uri-style=host --repo1-type=s3 --stanza=pg.pg
2023-10-11 15:14:44.983 P00   INFO: expire command end: completed successfully (428ms)
root@juju-7bca0e-3:~# cat  /var/snap/charmed-postgresql/common/var/log/pgbackrest/pg.pg-backup.log 
-------------------PROCESS START-------------------
2023-10-11 15:13:17.217 P00   INFO: backup command begin 2.47: --no-backup-standby --config=/var/snap/charmed-postgresql/current/etc/pgbackrest/pgbackrest.conf --exec-id=11725-9ad622c8 --lock-path=/tmp --log-level-console=debug --log-path=/var/snap/charmed-postgresql/common/var/log/pgbackrest --pg1-path=/var/snap/charmed-postgresql/common/var/lib/postgresql --pg1-socket-path=/tmp --pg1-user=backup --repo1-path=/postgresql-test2 --repo1-retention-full=9999999 --repo1-s3-bucket=dragop-test-bucket --repo1-s3-endpoint=https://s3.eu-central-1.amazonaws.com --repo1-s3-key=<redacted> --repo1-s3-key-secret=<redacted> --repo1-s3-region=eu-central-1 --repo1-s3-uri-style=host --repo1-type=s3 --stanza=pg.pg --start-fast --type=full
2023-10-11 15:13:18.269 P00   INFO: execute non-exclusive backup start: backup begins after the requested immediate checkpoint completes
2023-10-11 15:13:19.370 P00   INFO: backup start archive = 000000010000000000000003, lsn = 0/3000028
2023-10-11 15:13:19.370 P00   INFO: check archive for prior segment 000000010000000000000002
2023-10-11 15:14:40.970 P00   INFO: execute non-exclusive backup stop and wait for all WAL segments to archive
2023-10-11 15:14:41.273 P00   INFO: backup stop archive = 000000010000000000000003, lsn = 0/3000138
2023-10-11 15:14:41.641 P00   INFO: check archive for segment(s) 000000010000000000000003:000000010000000000000003
2023-10-11 15:14:42.478 P00   INFO: new backup label = 20231011-151318F
2023-10-11 15:14:44.555 P00   INFO: full backup size = 25.9MB, file total = 956
2023-10-11 15:14:44.555 P00   INFO: backup command end: completed successfully (87340ms)
```

## Logs rotation

Charmed PostgreSQL is configured to rotate PostgreSQL text logs every minute and Patroni logs approximately every minute and both are to retain a week's worth of logs.

For PostgreSQL, logs will be truncated when the week turns and the same minute of the same hour of the same weekday comes to pass. E.g. at 12:01 UTC on Monday either a new log file will be created or last week's log will be overwritten.

Due to Patroni only supporting size based rotation, it has been configured to retain logs for a comparatively similar timeframe as PostgreSQL. The assumed size of a minute of Patroni logs is 600 bytes, but the estimation is bound to be imprecise. Patroni will retain 10,080 log files (for every minute of a week). The current log is `patroni.log`, when rotating Patroni will append a number to the name of the file and remove logs over the limit.

