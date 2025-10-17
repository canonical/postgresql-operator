# Alert rules

This page contains a markdown version of the alert rules described in the `postgresql-operator` repository.  The following file(s) are the source of truth:
* [`src/prometheus_alert_rules/postgresql_rules.yaml`](https://github.com/canonical/postgresql-operator/blob/main/src/prometheus_alert_rules/postgresql_rules.yaml)
* [`src/prometheus_alert_rules/pgbouncer_rules.yaml`](https://github.com/canonical/postgresql-operator/blob/main/src/prometheus_alert_rules/pgbouncer_rules.yaml)
* [`src/prometheus_alert_rules/patroni_rules.yaml`](https://github.com/canonical/postgresql-operator/blob/main/src/prometheus_alert_rules/patroni_rules.yaml)

> This documentation describes the latest alert rule expressions. See the YAML file(s) listed above if you require an older version.

## PostgresqlExporter

| Alert | Severity | Notes |
|------|----------|-------|
| PostgresqlDown | ![critical] | PostgresSQL instance is down.<br>If you are not upgrading or configuring cross-region async replication clusters, check for errors in the Loki logs. |
| PostgresqlRestarted | ![info] | PostgresSQL instance has restarted.<br>If you are not enabling/disabling TLS or upgrading or configuring cross-region async replication clusters, check for errors in the Loki logs. |
| PostgresqlExporterError | ![critical] | PostgresSQL instance is showing an exporter error.<br>There may be a buggy query in query.yaml |
| PostgresqlTableNotAutoVacuumed | ![warning] | A PostgresSQL table in instance is not auto vacuumed.<br>A table has not been auto vacuumed for 7 days.Double-check your VACUUM settings. |
| PostgresqlTableNotAutoAnalyzed | ![warning] | A PostgresSQL table in instance is not auto analyzed.<br>A table has not been auto analyzed for 7 days.Double-check your AUTOVACUUM ANALYZE settings. |
| PostgresqlTooManyConnections | ![warning] | PostgresSQL instance is using > 80% of the maximum connections.<br>Consider checking how many connections the client application is opening, or using PgBouncer in front of the database. |
| PostgresqlNotEnoughConnections | ![info] | PostgresSQL instance does not have enough connections.<br>PostgreSQL instance should have more connections (> 5).<br>Consider double-checking how many connections the client application is opening and/or using PgBouncer in front of the database. |
| PostgresqlDeadLocks | ![warning] | PostgresSQL instance has dead locks.<br>See more details with the pg_locks view. |
| PostgresqlHighRollbackRate | ![warning] | PostgresSQL instance has a high rollback rate instance.<br>The ratio of transactions being aborted compared to committed is > 2 %.<br>This is probably happening due to unoptimised configurations related to commit delay, connections, memory, and WAL files. |
| PostgresqlCommitRateLow | ![info] | PostgresSQL instance has a low commit rate.<br>PostgresSQL seems to be processing very few transactions.<br>Check for long-running queries and configuration issues, like insufficient cache size. |
| PostgresqlLowXidConsumption | ![info] | PostgresSQL instance shows low XID consumption.<br>PostgresSQL seems to be consuming transaction IDs very slowly.<br>Run ANALYZE to update the optimizer statistics, ensure that query plans are correct, and double-check your VACUUM settings. |
| PostgresqlHighRateStatementTimeout | ![critical] | PostgresSQL instance shows a high rate of statement timeout.<br>Either tune `statement_timeout` when sending queries or use EXPLAIN ANALYZE to understand how the queries can be improved. |
| PostgresqlHighRateDeadlock | ![warning] | PostgresSQL instance shows a high deadlock rate.<br>More details can be obtained through the pg_locks view. |
| PostgresqlUnusedReplicationSlot | ![info] | PostgresSQL instance has unused replication slots.<br>Check if a replica is not using any of them before deleting it. |
| PostgresqlTooManyDeadTuples | ![warning] | PostgresSQL instance has too many dead tuples.<br>Double-check your VACUUM settings. |
| PostgresqlConfigurationChanged | ![info] | PostgresSQL instance configuration has changed.<br>PostgresSQL database configuration has changed. |
| PostgresqlSslCompressionActive | ![warning] | PostgresSQL instance SSL compression is active.<br>Database connections with SSL compression are enabled.<br>This may add significant jitter in replication delay.Replicas should turn off SSL compression via `sslcompression=0` in `recovery.conf`. |
| PostgresqlTooManyLocksAcquired | ![warning] | PostgreSQL instance has acquired too many locks.<br>If this alert happens frequently, you may need to increase the PostgresSQL setting max_locks_per_transaction. |
| PostgresqlBloatIndexHigh(>80%) | ![warning] | PostgreSQL instance has a high bloat index (> 80%).<br>An index is bloated.Consider running `REINDEX INDEX CONCURRENTLY <index name>;` |
| PostgresqlBloatTableHigh(>80%) | ![warning] | PostgreSQL instance has a high bloat table (> 80%).<br>A table is bloated.Consider running `VACUUM {{ $labels.relname }};` |
| PostgresqlInvalidIndex | ![critical] | PostgresSQL instance )= has an invalid index.<br>A table has an invalid index.<br>Consider running `DROP INDEX <index name>;` |

## PgbouncerExporter

| Alert | Severity | Notes |
|------|----------|-------|
| PgbouncerActiveConnections | ![warning] | PgBouncer instance has > 200 active connections<br>Consider checking the client application responsible for generating those additional connections. |
| PgbouncerErrors | ![warning] | PgBouncer instance is logging errors.<br>This may be due to a a server restart or an admin typing commands at the PgBouncer console. |
| PgbouncerMaxConnections | ![critical] | PgBouncer instance has reached `max_client_conn`.<br>Consider checking how many connections the client application is opening. |

## PatroniExporter

| Alert | Severity | Notes |
|------|----------|-------|
| PatroniPostgresqlDown | ![critical] | Patroni PostgreSQL instance is down.<br>Check for errors in the Loki logs. |
| PatroniHasNoLeader | ![critical] | Patroni instance has no leader node.<br>A leader node (neither primary nor standby) cannot be found inside a cluster.<br>Check for errors in the Loki logs. |

## `PgbackrestExporter`

| Alert | Severity | Notes |
| ----- | -------- | ----- |
| `PgBackRestBackupError` | ![critical] | Backup failed for a stanza.<br>The last pgBackRest backup ended with error status > 0.<br>Check the pgBackRest logs for the stanza. |
| `PgBackRestBackupTooOld` | ![warning] | No recent backup available.<br>The last pgBackRest backup is older than 7 days.<br>Consider checking your backup schedule, capacity, and logs. |
| `PgBackRestStanzaError` | ![warning] | A stanza has reported errors.<br>Status > 0 indicates problems such as missing stanza path or no valid backups.<br>Check pgBackRest logs for details. |
| `PgBackRestRepoError` | ![warning] | A repository has reported errors.<br>Status > 0 indicates the repo may be inaccessible, out of space, or otherwise unhealthy.<br>Check pgBackRest logs and storage system. |
| `PgBackRestExporterError` | ![critical] | The pgBackRest exporter failed to fetch data.<br>Metric `pgbackrest_exporter_status == 0` indicates exporter-side issues.<br>This may be a misconfiguration or runtime error; check exporter logs. |

<!-- Badges -->
[info]: https://img.shields.io/badge/info-blue
[warning]: https://img.shields.io/badge/warning-yellow
[critical]: https://img.shields.io/badge/critical-red
