# How to migrate a cluster

This is a guide on how to restore a backup that was made from a different cluster, (i.e. cluster migration via restore). 

To perform a basic restore (from a *local* backup), see [](/how-to/back-up-and-restore/restore-a-backup).

## Prerequisites

Restoring a backup from a previous cluster to a current cluster requires:
* A single unit Charmed PostgreSQL deployed and running
* Backups from the previous cluster in your S3 storage
  * See: {ref}`create-a-backup`
* Saved credentials from your previous cluster
  * See: {ref}`manage-passwords` and {ref}`save-current-cluster-credentials`

## Apply cluster credentials

Charmed PostgreSQL will enable authentication by default after restoring a backup, and it will automatically generate admin user passwords if none are provided.

To make sure it uses the credentials from the previous cluster, we must apply the credentials you {ref}`saved during the backup process <save-current-cluster-credentials>` before restoring.

<!--begin include-->
Create a secret with the password values you saved when creating the backup:

```shell
juju add-secret <secret name> monitoring=<password1> operator=<password2> replication=<password3> rewind=<password4>
```

where `<secret name>` can be any name you'd like for the restored secrets.

Then, grant the secret to the `postgresql` application that will initiate the restore:

```shell
juju grant-secret <secret name> postgresql
```
<!--end include-->

## List backups

To view the available backups to restore, use the command `list-backups`:

```shell
juju run postgresql/leader list-backups 
```

This shows a list of the available backups (it is up to you to identify which `backup-id` corresponds to the previous cluster):

```text
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
