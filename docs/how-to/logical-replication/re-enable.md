# How to re-enable logical replication

If the relation between the PostgreSQL clusters is broken, you can re-enable logical replication by following these steps.

Drop and re-create the table on the second cluster:
```sh
psql postgresql://relation-9:FHZbyAPGQjbDpj65@10.166.227.109:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> drop table asd; create table asd (message int);
DROP TABLE
CREATE TABLE
```

Then, integrate the clusters again:
```sh
juju relate postgresql1:logical-replication-offer postgresql2:logical-replication
```

And you'll be able to see the data replicated from the first cluster to the second:
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
