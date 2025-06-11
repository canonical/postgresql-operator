# Roles 

There are several definitions of roles in Charmed PostgreSQL:
* Predefined PostgreSQL roles
* Instance level DB/relation-specific roles
  *  LDAP-specific roles 
* Extra user roles relation flag

```{seealso}
[](/explanation/users)
```

## PostgreSQL 16 roles

```text
test123=> SELECT * FROM pg_roles;
           rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid  
-----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
 pg_database_owner           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6171
 pg_read_all_data            | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6181
 pg_write_all_data           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6182
 pg_monitor                  | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3373
 pg_read_all_settings        | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3374
 pg_read_all_stats           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3375
 pg_stat_scan_tables         | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3377
 pg_read_server_files        | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4569
 pg_write_server_files       | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4570
 pg_execute_server_program   | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4571
 pg_signal_backend           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4200
 pg_checkpoint               | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4544
 pg_use_reserved_connections | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4550
 pg_create_subscription      | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6304
...
```

## Charmed PostgreSQL 16 roles

Charmed PostgreSQL 16 introduces the following instance level predefined roles:

* charmed_stats (inherit from pg_monitor)
* charmed_read (inherit from pg_read_all_data)
* charmed_dml (inherit from pg_write_all_data)
* charmed_backup (inherit from pg_checkpoint)
* charmed_dba (WIP)
* charmed_instance_admin (WIP)

```text
test123=> SELECT * FROM pg_roles;
           rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid  
-----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 charmed_stats               | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16386
 charmed_read                | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16388
 charmed_dml                 | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16390
 charmed_backup              | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16392
...
```

<!--TODO: are the next two sections also relevant for 16?-->
### Relation specific roles

For each application/relation the dedicated user is been created (with matching role and all all resources ownership). The resources ownership is being updated on each re-relation for new users/roles regeneration. Example of simple application relation to PostgreSQL and creating table:

```text
postgres=# SELECT * FROM pg_roles;
          rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid  
----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 relation_id_12             | f        | t          | t             | t           | t           | f              |           -1 | ********    |               | f            |           | 16416
...

postgres=# SELECT * FROM pg_user;
          usename           | usesysid | usecreatedb | usesuper | userepl | usebypassrls |  passwd  | valuntil | useconfig 
----------------------------+----------+-------------+----------+---------+--------------+----------+----------+-----------
 ...
 relation_id_12             |    16416 | t           | f        | f       | f            | ******** |          | 
...

mydb=# \d+
             List of relations
 Schema |  Name   | Type  |     Owner      | ...
--------+---------+-------+----------------+ ...
 public | mytable | table | relation_id_12 | ...

```

When the same application is being related through PgBouncer, the extra users/roles created following the same logic as above:

```text
postgres=# SELECT * FROM pg_roles;
          rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid  
----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 relation-14                | t        | t          | f             | f           | t           | f              |           -1 | ********    |               | f            |           | 16403
 pgbouncer_auth_relation_14 | t        | t          | f             | f           | t           | f              |           -1 | ********    |               | f            |           | 16410
 relation_id_13             | f        | t          | t             | t           | t           | f              |           -1 | ********    |               | f            |           | 16417
...

postgres=# SELECT * FROM pg_user;
          usename           | usesysid | usecreatedb | usesuper | userepl | usebypassrls |  passwd  | valuntil | useconfig 
----------------------------+----------+-------------+----------+---------+--------------+----------+----------+-----------
 ...
 relation-14                |    16403 | f           | t        | f       | f            | ******** |          | 
 pgbouncer_auth_relation_14 |    16410 | f           | t        | f       | f            | ******** |          | 
 relation_id_13             |    16417 | t           | f        | f       | f            | ******** |          | 
...

mydb=# \d+
               List of relations
 Schema |  Name   | Type  |     Owner      | ... 
--------+---------+-------+----------------+ ...
 public | mytable | table | relation_id_13 | ...
```

In this case there several records created to:
 * `relation_id_13` - for relation between Application and PgBouncer
 * `relation-14` - for relation between PgBouncer and PostgreSQL
 * `pgbouncer_auth_relation_14` - to authenticate end-users which connects PgBouncer

### Charmed PostgreSQL LDAP roles

To map LDAP users to PostgreSQL users, the dedicated LDAP groups have to be created before hand using [Data Integrator](https://charmhub.io/data-integrator) charm.
The result of such mapping will be a new PostgreSQL Roles:

```text
postgres=# SELECT * FROM pg_roles;
    rolname    | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid  
----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 myrole        | t        | t          | f             | f           | t           | f              |           -1 | ********    |               | f            |           | 16422
```

