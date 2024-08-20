[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# How to restore a local backup 

This is a guide on how to restore a locally made backup.

To restore a backup that was made from a *different* cluster, (i.e. cluster migration via restore), see [How to migrate cluster using backups](/t/charmed-postgresql-how-to-migrate-cluster-via-restore/9691?channel=14/stable):


## Prerequisites
- Deployments have been [scaled-down](/t/charmed-postgresql-how-to-manage-units/9689?channel=14/stable) to a single PostgreSQL unit (scale it up after the backup is restored)
- Access to S3 storage
- [Configured settings for S3 storage](/t/charmed-postgresql-how-to-configure-s3/9681?channel=14/stable)
- [Existing backups in your S3storage](/t/charmed-postgresql-how-to-create-and-list-backups/9683?channel=14/stable)

---

## List backups
To view the available backups to restore, use the command `list-backups`:
```shell
juju run postgresql/leader list-backups
```

This should show your available backups like in the sample output below:
```shell
    backups: |-
      backup-id             | backup-type  | backup-status
      ----------------------------------------------------
      YYYY-MM-DDTHH:MM:SSZ  | physical     | finished
```
## Restore backup
To restore a backup from that list, run the `restore` command and pass the corresponding `backup-id`:
 ```shell
juju run postgresql/leader restore backup-id=YYYY-MM-DDTHH:MM:SSZ
```

Your restore will then be in progress.