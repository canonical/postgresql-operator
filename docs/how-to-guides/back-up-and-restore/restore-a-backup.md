(how-to-guides-back-up-and-restore-restore-a-backup)=


```{note}
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#juju-3-0-0---22-oct-2022).
```

# How to restore a local backup

This is a guide on how to restore a locally made backup.

To restore a backup that was made from a *different* cluster, (i.e. cluster migration via restore), see [How to migrate cluster using backups](/how-to-guides/back-up-and-restore/migrate-a-cluster):

## Prerequisites
- Deployments have been [scaled-down](/how-to-guides/scale-replicas) to a single PostgreSQL unit (scale it up after the backup is restored)
- Access to S3 storage
- [Configured settings for S3 storage](/)
- [Existing backups in your S3 storage](/how-to-guides/back-up-and-restore/create-a-backup)
- [Point-in-time recovery](#point-in-time-recovery) requires the following PostgreSQL charm revisions:
   - 467+ for `arm64`
  -  468+ for `amd64`

## Summary
* [List backups](#list-backups)
* [Point-in-time recovery](#point-in-time-recovery)
* [Restore backup](#restore-backup)

---

## List backups
To view the available backups to restore, use the command `list-backups`:
```shell
juju run postgresql/leader list-backups
```

This should show your available backups like in the sample output below:
<!--TODO: Update output with any missing parameters (id, repository, etc...) -->
```shell
list-backups: |-
  Storage bucket name: canonical-postgres
  Backups base path: /test/backup/

  backup-id            | action             | ... | timeline
