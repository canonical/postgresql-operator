# Migrate database data using `pg_dump` / `pg_restore`

This guide describes database **data** migration only. To migrate charms on new juju interfaces, refer to the guide [How to integrate a database with my charm](/how-to/development/integrate-with-your-charm). 

```{note}
All commands are written for `juju >= v.3.0`

If you are using an earlier version, be aware that:

 - `juju run` replaces `juju run-action --wait` in `juju v.2.9` 
 - `juju integrate` replaces `juju relate` and `juju add-relation` in `juju v.2.9`

For more information, check the [Juju 3.0 Release Notes](https://documentation.ubuntu.com/juju/3.6/reference/juju/juju-roadmap-and-releases/#juju-3-0-0-22-oct-2022).
```

## Do you need to migrate?

A database migration is only required if the output of the following command is `latest/stable`:

```text
juju show-application postgresql | yq '.[] | .channel'
```
Migration is **not** necessary if the output above is `14/stable`! 

This guide can be used to copy data between different installations of the same (modern) charm `postgresql`, but the [backup/restore](/how-to/development/migrate-data-via-backup-restore) is more recommended for migrations between modern charms.

## Summary

The legacy VM charm archived in the `latest/stable` channel, read more [here](/explanation/legacy-charm).
A minor difference in commands might be necessary for different revisions and/or Juju versions, but the general logic remains:

* Deploy the modern charm nearby
* Request credentials from legacy charm
* Remove relation to legacy charm (to stop data changes)
* Perform legacy DB dump (using the credentials above)
* Upload the legacy charm dump into the modern charm
* Add relation to modern charm
* Validate results and remove legacy charm

```{caution}
Always test migration in a safe environment before performing it in production!
```

## Prerequisites

- **[Your application is compatible](/explanation/legacy-charm) with Charmed PostgreSQL VM**
- A client machine with access to the deployed legacy charm
- `juju v.2.9` or later  (check [Juju 3.0 Release Notes](https://documentation.ubuntu.com/juju/3.6/reference/juju/juju-roadmap-and-releases/#juju-3-0-0-22-oct-2022) for more information about key differences)
- Enough storage in the cluster to support backup/restore of the databases.

## Obtain existing database credentials

To obtain credentials for existing databases, execute the following commands for **each** database that will be migrated. Take note of these credentials for future steps.

First, define and tune your application and db (database) names. For example:
```text
CLIENT_APP=< my-application/0 >
OLD_DB_APP=< legacy-postgresql/leader | postgresql/0 >
NEW_DB_APP=< new-postgresql/leader | postgresql/0 >
DB_NAME=< your_db_name_to_migrate >
```
Then, obtain the username from the existing legacy database via its relation info:
```text
OLD_DB_USER=$(juju show-unit ${CLIENT_APP} | yq '.[] | .relation-info | select(.[].endpoint == "db") | .[0].application-data.user')
```

## Deploy new PostgreSQL databases and obtain credentials

Deploy new PostgreSQL database charm:

```text
juju deploy postgresql ${NEW_DB_APP} --channel 16/stable
```
Obtain `operator` user password of new PostgreSQL database from PostgreSQL charm:
```text
NEW_DB_USER=operator
NEW_DB_PASS=$(juju run ${NEW_DB_APP} get-password | yq '.password')
```

## Migrate database
Use the credentials and information obtained in previous steps to perform the database migration with the following procedure.

```{note}
Make sure no new connections were made and that the database has not been altered!
```

### Create dump from legacy charm
Remove the relation between application charm and legacy charm:
```text
juju remove-relation  ${CLIENT_APP}  ${OLD_DB_APP}
```
Connect to the database VM of a legacy charm:
```text
juju ssh ${OLD_DB_APP} bash
```
Create a dump via Unix socket using credentials from the relation:
```text
mkdir -p /srv/dump/
OLD_DB_DUMP="legacy-postgresql-${DB_NAME}.sql"
pg_dump -Fc -h /var/run/postgresql/ -U ${OLD_DB_USER} -d ${DB_NAME} > "/srv/dump/${OLD_DB_DUMP}"
```
Exit the database VM:
```text
exit
```
### Upload dump to new charm
Fetch dump locally and upload it to the new Charmed PostgreSQL charm:
```text
juju scp ${OLD_DB_APP}:/srv/dump/${OLD_DB_DUMP}  ./${OLD_DB_DUMP}
juju scp ./${OLD_DB_DUMP}  ${NEW_DB_APP}:.
```
ssh into new Charmed PostgreSQL charm and create a new database (using `${NEW_DB_PASS}`):
```text
juju ssh ${NEW_DB_APP} bash
createdb -h localhost -U ${NEW_DB_USER} --password ${DB_NAME}
```
Restore the dump (using `${NEW_DB_PASS}`):
```text
pg_restore -h localhost -U ${NEW_DB_USER} --password -d ${DB_NAME} --no-owner --clean --if-exists ${OLD_DB_DUMP}
```

## Integrate with modern charm
Integrate (formerly "relate" in `juju v.2.9`) your application and new PostgreSQL database charm (using the modern `database` endpoint)
```text
juju integrate ${CLIENT_APP}  ${NEW_DB_APP}:database
```

If the `database` endpoint (from the `postgresql_client` interface) is not yet supported, use instead the `db` endpoint from the legacy `pgsql` interface:
```text
juju integrate ${CLIENT_APP}  ${NEW_DB_APP}:db
```

## Verify database migration

Test your application to make sure the data is available and in a good condition.

## Remove old databases

Test your application and if you are happy with a data migration, do not forget to remove legacy charms to keep the house clean:

```text
juju remove-application --destroy-storage <legacy_postgresql>
```

