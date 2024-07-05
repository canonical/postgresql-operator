[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# How to migrate a cluster

This is a guide on how to restore a backup that was made from a different cluster, (i.e. cluster migration via restore). 

To perform a basic restore (from a *local* backup), see [Charmed PostgreSQL VM | How to restore a local backup](/t/charmed-postgresql-how-to-restore-backup/9693?channel=14/stable).

## Prerequisites
Restoring a backup from a previous cluster to a current cluster requires:
- A single unit Charmed PostgreSQL deployed and running
- Access to S3 storage
- [Configured settings for S3 storage](/t/charmed-postgresql-how-to-configure-s3/9681?channel=14/stable)
- Backups from the previous cluster in your S3 storage
- Passwords from your previous cluster

---

<a href="#heading--manage-cluster-passwords"><h2 id="heading--manage-cluster-passwords">Manage cluster passwords</h2></a>
When you restore a backup from an old cluster, it will restore the password from the previous cluster to your current cluster. Set the password of your current cluster to the previous cluster’s password:
```shell
juju run postgresql/leader set-password username=operator password=<previous cluster password> 
juju run postgresql/leader set-password username=replication password=<previous cluster password> 
juju run postgresql/leader set-password username=rewind password=<previous cluster password> 
```
## List backups
To view the available backups to restore, use the command `list-backups`:
```shell
juju run postgresql/leader list-backups 
```
This shows a list of the available backups (it is up to you to identify which `backup-id` corresponds to the previous-cluster):
```shell
    backups: |-
      backup-id             | backup-type  | backup-status
      ----------------------------------------------------
      YYYY-MM-DDTHH:MM:SSZ  | physical     | finished
```

## Restore backup
To restore your current cluster to the state of the previous cluster, run the `restore` command and pass the correct `backup-id` to the command:
 ```shell
juju run postgresql/leader restore backup-id=YYYY-MM-DDTHH:MM:SSZ 
```

Your restore will then be in progress. Once it is complete, your current cluster will represent the state of the previous cluster.