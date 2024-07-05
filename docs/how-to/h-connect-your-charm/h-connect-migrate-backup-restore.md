# Migrate database data using ‘backup/restore’

This is a guide for migrating data from modern charms. To migrate [legacy charms](/t/11013) data, refer to the guide [Migrate data via pg_dump](/t/10690).

This Charmed PostgreSQL operator is able to restore its own[backups](/t/9693) stored on [S3-compatible storage](/t/9681). The same restore approach is applicable to restore [foreign backups](/t/9691) made by different Charmed PostgreSQL installation or even another PostgreSQL charm. The backup have to be created manually using [pgBackRest](https://pgbackrest.org/)!

[note type="caution"]
**Warning:** The Canonical Data Team describes here the general approach and does NOT support nor guarantee the restoration results. 

Always test a migration in a test environment before performing it in production!
[/note]

## Prerequisites
* **Check [your application compatibility](/t/10690)** with Charmed PostgreSQL VM before migrating production data from legacy charm
* Make sure **PostgreSQL versions are identical** before the migration

## Migrate database data
Below is the *general approach* to the migration (see warning above!):

1. **Retrieve root/admin level credentials from legacy charm.** 

   See examples [here](/t/12163).

2. **Install [pgBackRest](https://pgbackrest.org/) inside the old charm OR nearby.** 

    Ensure the version is compatible with pgBackRest in the new `Charmed PostgreSQL` revision you are going to deploy! See examples [here](https://pgbackrest.org/user-guide.html#installation).

   **Note**: You can use `charmed-postgresql` [SNAP](https://snapcraft.io/charmed-postgresql)/[ROCK](https://github.com/canonical/charmed-postgresql-rock) directly. More details [here](/t/11857#hld).

3. **Configure storage for database backup (local or remote, S3-based is recommended).**

4. **Create a first full logical backup during the off-peak** 

   See an example of backup command [here](https://github.com/canonical/postgresql-k8s-operator/commit/f39caaa4c5c85afdb157bd53df54a24a1b9687ac#diff-cc5993b9da2438ecff27897b3ab9d2f9bc445cbf5b4f6369a1a0c2f404fe6a4fR186-R212).

5. **[Restore the foreign backup](/t/9691) to the Charmed PostgreSQL installation in your test environment.**
6. **Perform all the necessary tests to make sure your application accepted the new database.**
7. **Schedule and perform the final production migration, re-using the last steps above.**

---
Do you have questions? [Contact us](/t/11863) if you are interested in such a data migration!