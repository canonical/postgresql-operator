# How to set up clusters for logical replication

```{caution}
This feature is only available for revision 863 or higher, which is not yet in the stable track.
```

Start by deploying two PostgreSQL clusters:
```sh
juju deploy postgresql --channel 16/edge postgresql1
juju deploy postgresql --channel 16/edge postgresql2
```

For testing purposes, you can deploy two applications of the [data integrator charm](https://charmhub.io/data-integrator) and then integrate them to the two PostgreSQL clusters you want to replicate data between.
```sh
juju deploy data-integrator di1 --config database-name=testdb
juju deploy data-integrator di2 --config database-name=testdb

juju integrate postgresql1 di1
juju integrate postgresql2 di2
```

Then, integrate both PostgreSQL clusters:
```sh
juju integrate postgresql1:logical-replication-offer postgresql2:logical-replication
```

This will create a publication on the first cluster and a subscription on the second cluster, allowing data to be replicated from the first to the second.

Request the credentials for the first PostgreSQL cluster.
```sh
juju run di1/leader get-credentials
```

The output example:
```yaml
postgresql:
  data: '{"database": "testdb", "external-node-connectivity": "true", "provided-secrets":
    "[\"mtls-cert\"]", "requested-secrets": "[\"username\", \"password\", \"tls\",
    \"tls-ca\", \"uris\", \"read-only-uris\"]"}'
  database: testdb
  endpoints: 10.166.227.78:5432
  password: G7Qu77SU0qeadnhn
  read-only-endpoints: 10.166.227.78:5432
  read-only-uris: postgresql://relation-8:G7Qu77SU0qeadnhn@10.166.227.78:5432/testdb
  tls: "False"
  tls-ca: ""
  uris: postgresql://relation-8:G7Qu77SU0qeadnhn@10.166.227.78:5432/testdb
  username: relation-8
  version: "16.9"
```

Then create a table and insert some data into it on the first cluster:
```sh
psql postgresql://relation-8:G7Qu77SU0qeadnhn@10.166.227.78:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> create table asd (message int); insert into asd values (123);
CREATE TABLE
INSERT 0 1
```

After that, you need to create the same table on the second cluster so that the data can be replicated. Start by getting the credentials for the second cluster:
```sh
juju run di2/leader get-credentials
```

The output example:
```yaml
postgresql:
  data: '{"database": "testdb", "external-node-connectivity": "true", "provided-secrets":
    "[\"mtls-cert\"]", "requested-secrets": "[\"username\", \"password\", \"tls\",
    \"tls-ca\", \"uris\", \"read-only-uris\"]"}'
  database: testdb
  endpoints: 10.166.227.109:5432
  password: FHZbyAPGQjbDpj65
  read-only-endpoints: 10.166.227.109:5432
  read-only-uris: postgresql://relation-9:FHZbyAPGQjbDpj65@10.166.227.109:5432/testdb
  tls: "False"
  tls-ca: ""
  uris: postgresql://relation-9:FHZbyAPGQjbDpj65@10.166.227.109:5432/testdb
  username: relation-9
  version: "16.9"
```

Then create the same table on the second cluster:
```sh
psql postgresql://relation-9:FHZbyAPGQjbDpj65@10.166.227.109:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> create table asd (message int);
CREATE TABLE
```

Configure the replication of that specific database and table (remember to specify the table schema; it's the `public` schema in this example):
```sh
juju config postgresql2 logical_replication_subscription_request='{"testdb": ["public.asd"]}'
```

After a few seconds, you can check that the data has been replicated:
```sh
psql postgresql://relation-9:FHZbyAPGQjbDpj65@10.166.227.109:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> select * from asd;
 message
---------
     123
(1 row)
```

You can then add more data to the table in the first cluster, and it will be replicated to the second cluster automatically.

If the relation between the PostgreSQL clusters is broken, the data will be kept in both clusters, but the replication will stop. You can re-enable logical replication by following the steps from [](/how-to/logical-replication/re-enable).
