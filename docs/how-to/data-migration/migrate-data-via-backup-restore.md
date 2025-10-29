(migrate-data-via-backup-restore)=
# Migrate data via backup/restore

This is a guide for migrating data from modern charms. To migrate [legacy charms](/explanation/charm-versions/legacy-charm) data, refer to the guide [Migrate data via pg_dump](/how-to/development/migrate-data-via-pg-dump).

This Charmed PostgreSQL operator is able to restore its own [backups](/how-to/back-up-and-restore/restore-a-backup) stored on [S3-compatible storage](/how-to/back-up-and-restore/configure-s3-aws). The same restore approach is applicable to restore [foreign backups](/how-to/back-up-and-restore/migrate-a-cluster) made by different Charmed PostgreSQL installation or even another PostgreSQL charm. The backup has to be created manually using [pgBackRest](https://pgbackrest.org/)!

```{caution}
The Canonical Data Team describes here the general approach and does NOT support nor guarantee the restoration results. 

Always test a migration in a test environment before performing it in production!
```

## Prerequisites
* **Check [your application compatibility](/explanation/charm-versions/index)** with Charmed PostgreSQL VM before migrating production data from legacy charm
* Make sure **PostgreSQL versions are identical** before the migration

## Migrate database data
Below is the *general approach* to the migration (see warning above!):

1. Retrieve root/admin level credentials from legacy charm. 

   See examples in [](/how-to/development/migrate-data-via-pg-dump).

2. Install [pgBackRest](https://pgbackrest.org/) inside the old charm OR nearby. 

    Ensure the version is compatible with pgBackRest in the new `Charmed PostgreSQL` revision you are going to deploy! See examples [here](https://pgbackrest.org/user-guide.html#installation).

   ```{note}
   You can use the `charmed-postgresql` [snap](https://snapcraft.io/charmed-postgresql)/[ROCK](https://github.com/canonical/charmed-postgresql-rock) directly. More details [here](/explanation/architecture)
   ```

3. Configure storage for database backup (local or remote, S3-based is recommended).

4. Create a first full logical backup during the off-peak 

   See an example of backup command [here](https://github.com/canonical/postgresql-k8s-operator/commit/f39caaa4c5c85afdb157bd53df54a24a1b9687ac#diff-cc5993b9da2438ecff27897b3ab9d2f9bc445cbf5b4f6369a1a0c2f404fe6a4fR186-R212).

5. [Restore the foreign backup](/how-to/back-up-and-restore/migrate-a-cluster) to the Charmed PostgreSQL installation in your test environment.
6. Perform all the necessary tests to make sure your application accepted the new database.
7. Schedule and perform the final production migration, re-using the last steps above.

---

Do you have questions? [Contact us](/reference/contacts) if you are interested in such a data migration!

