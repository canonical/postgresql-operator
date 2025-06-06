# How to enable TimescaleDB

[Charmed PostgreSQL 14](https://charmhub.io/postgresql?channel=14/stable) ships [Timescale Apache 2 edition](https://docs.timescale.com/about/latest/timescaledb-editions/).

## Enable TimescaleDB

To enable TimescaleDB plugin/extension simply run:

```text
juju config postgresql plugin_timescaledb_enable=true
```

The plugin has been enabled on all units once the config-change event finished and all units reports idle:

```text
> juju status
...
Unit           Workload  Agent      Machine  Public address  Ports     Message
postgresql/3*  active    executing  3        10.189.210.124  5432/tcp  (config-changed) Primary
postgresql/5   active    executing  5        10.189.210.166  5432/tcp  (config-changed) 
postgresql/6   active    executing  6        10.189.210.150  5432/tcp  (config-changed) 
...
Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/3*  active    idle   3        10.189.210.124  5432/tcp  Primary
postgresql/5   active    idle   5        10.189.210.166  5432/tcp  
postgresql/6   active    idle   6        10.189.210.150  5432/tcp  
...
```

## Disable TimescaleDB

To disable it explicitly, simply run:

```text
juju config postgresql plugin_timescaledb_enable=false
```
The plugin has been disabled on all units once the config-change event finished and all units reports idle (same example as above).

The extension will NOT be disabled when database objects uses/depends on plugin is being disabled (clean the database to disable the plugin):

```text
> juju status
...
Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/3*  blocked   idle   3        10.189.210.124  5432/tcp  Cannot disable plugins: Existing objects depend on it. See logs
...
```

Another option is to reset the manually enabled config option (as it is disabled by default):
```text
juju config postgresql --reset plugin_timescaledb_enable
```

## Test TimescaleDB status

Prepare the `user_defined_action` procedure:

```text
postgres=# CREATE OR REPLACE PROCEDURE user_defined_action(job_id int, config jsonb) LANGUAGE PLPGSQL AS
$$
BEGIN
  RAISE NOTICE 'Executing action % with config %', job_id, config;
END
$$;
```

Run the following commands to test your TimescaleDB on Charmed PostgreSQL 14:

```text
postgres=# SELECT add_job('user_defined_action','1h');
ERROR:  function "add_job" is not supported under the current "apache" license
HINT:  Upgrade your license to 'timescale' to use this free community feature.

postgres=# CREATE TABLE test_timescaledb (time TIMESTAMPTZ NOT NULL); SELECT create_hypertable('test_timescaledb', 'time');
CREATE TABLE
       create_hypertable       
