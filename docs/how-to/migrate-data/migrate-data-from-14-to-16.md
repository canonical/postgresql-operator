# Migrate data from PostgreSQL 14 to 16

There are two possible ways to migrate data from PostgreSQL 14 to 16 depending on how {ref}`roles` are managed:

**In the case of charm roles management**, all the database objects ownership will be handled by charm automatically. This is what this guide will cover.

**In case of admin roles management**, all database objects ownership are handled manually by the user. See: {ref}`migrate-data-via-pg-dump`.

<!--TODO: Should this note be transferred to pgdump guide? -->
Note: make sure `extra-user-roles` set to `charmed-admin` once Juju relation requested to such DB. <!--TODO: the new db?-->

This guide covers how to migrate data from Charmed PostgreSQL 14 to 16 using the new charm roles management setup for client applications managed by Juju. The migrated data from PostgreSQL 14 will be mapped to the corresponding ownership in PostgreSQL 16. 

## Prepare PostgreSQL 14 data

First, in order to make sure the latest data is written to your dump, remove the relation between the client app and Charmed PostgreSQL 14.

Define the following variables for the old database:

```shell
TMP_PATH="~/old-db-dump/"
OLD_DB_NAME="postgresql_test_app_database"
OLD_IP="10.218.34.229"
OLD_USER="operator"
OLD_PASSWORD="fJQYljbthEo2T1gj"
```

Create a dump of the old PostgreSQL 14 database with `pg_dump`:

```shell
mkdir -p "${TMP_PATH}"
PGPASSWORD="${OLD_PASSWORD}" pg_dump -j 4 -Fd -h "${OLD_IP}" -U "${OLD_USER}" -d "${OLD_DB_NAME}" -f "${TMP_PATH}" --compress 9
```

## Set up new PostgreSQL 16 charm

Deploy one unit of Charmed PostgreSQL 16. This will simplify the migration and can be scaled later.

```shell
juju deploy postgresql --channel 16/stable
```

Define the following variables for the new database:

```shell
NEW_DB_NAME="postgresql_test_app_database123"
NEW_IP="10.218.34.56"
NEW_USER="operator"
NEW_PASSWORD="RnnijCiotVeW8O5I"
NEW_OWNER="charmed_${NEW_DB_NAME}_owner"
```

Migrate the following charm features from the old 14 charm to the new 16 charm: 
* any necessary charm config options
* enabled charm extensions/plugins

```{note}
Config options and extensions *must* be migrated before restoring the data dump
```

## Create a new database on PostgreSQL 16

```shell
PGPASSWORD="${NEW_PASSWORD}" psql -h "${NEW_IP}" -U "${NEW_USER}" -d postgres -c "CREATE DATABASE ${NEW_DB_NAME}"
```

Create new roles by running the function `set_up_predefined_catalog_roles()` in all databases except `postgres` and `template1`.  It will create roles like `charmed_<database-name>_owner`, `..._dml` and others:

```shell
PGPASSWORD="${NEW_PASSWORD}" psql -h "${NEW_IP}" -U "${NEW_USER}" -d "${NEW_DB_NAME}" -c "SELECT set_up_predefined_catalog_roles();"
PGPASSWORD="${NEW_PASSWORD}" psql -h "${NEW_IP}" -U "${NEW_USER}" -d "${NEW_DB_NAME}" -c "ALTER DATABASE ${NEW_DB_NAME} OWNER TO charmed_databases_owner;"
PGPASSWORD="${NEW_PASSWORD}" psql -h "${NEW_IP}" -U "${NEW_USER}" -d "${NEW_DB_NAME}" -c "ALTER SCHEMA public OWNER TO ${NEW_OWNER};"
```

## Migrate data from PostgreSQL 14 to 16

Restore the PostgreSQL 14 database dump into the new 16 database:

```shell
PGPASSWORD="${NEW_PASSWORD}" pg_restore -j 4 -h "${NEW_IP}" -U "${NEW_USER}" -d "${NEW_DB_NAME}" -Fd "${TMP_PATH}" --no-owner
```

### Set up new database ownership

Verify and modify the ownership for each database object in each schema to be equal to `charmed_<database-name>_owner` (`${NEW_OWNER}` above).

For example, to find and fix ownership for all tables, sequences, and views:  

```shell
PGPASSWORD="${NEW_PASSWORD}" psql -h "${NEW_IP}" -U "${NEW_USER}" -d "${NEW_DB_NAME}"

mydb=> DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT format('ALTER %s %I.%I OWNER TO %I;',
                  CASE c.relkind
                    WHEN 'r' THEN 'TABLE'
                    WHEN 'v' THEN 'VIEW'
                    WHEN 'm' THEN 'MATERIALIZED VIEW'
                    WHEN 'S' THEN 'SEQUENCE'
                    WHEN 'p' THEN 'TABLE'
                    ELSE NULL END,
                  n.nspname, c.relname, 'charmed_<database-name>_owner')
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
  LOOP
    EXECUTE r.format;
  END LOOP;
END$$;
```

At this stage, the database has been completely imported. The cluster can be scaled, and the client app can be related to the new PostgreSQL 16 database.

contact