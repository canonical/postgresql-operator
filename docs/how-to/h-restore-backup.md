This is a How-To for performing a basic restore (restoring a locally made backup).
To restore a backup that was made from the a *different* cluster, (i.e. cluster migration via restore), please reference the [Cluster Migration via Restore How-To](/t/charmed-postgresql-how-to-migrate-cluster-via-restore/9691?channel=14/stable):

Restoring from a backup requires that you:
- [Scale-down to the single PostgreSQL unit (scale it up after the backup is restored).](/t/charmed-postgresql-how-to-manage-units/9689?channel=14/stable)
- Access to S3 storage
- [Have configured settings for S3 storage](/t/charmed-postgresql-how-to-configure-s3/9681?channel=14/stable)
- [Have existing backups in your S3-storage](/t/charmed-postgresql-how-to-create-and-list-backups/9683?channel=14/stable)

To view the available backups to restore you can enter the command `list-backups`:
```shell
juju run-action postgresql/leader list-backups --wait
```

This should show your available backups
```shell
    backups: |-
      backup-id             | backup-type  | backup-status
      ----------------------------------------------------
      YYYY-MM-DDTHH:MM:SSZ  | physical     | finished
```

To restore a backup from that list, run the `restore` command and pass the `backup-id` to restore:
 ```shell
juju run-action postgresql/leader restore backup-id=YYYY-MM-DDTHH:MM:SSZ --wait
```

Your restore will then be in progress.