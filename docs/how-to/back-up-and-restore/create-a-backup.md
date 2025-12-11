# How to create and list backups

This guide contains recommended steps and useful commands for creating and managing backups to ensure smooth restores.

## Prerequisites
* A cluster with at [least three nodes](/how-to/scale-replicas) deployed
* Access to S3 storage
* [Configured settings for S3 storage](/how-to/back-up-and-restore/configure-s3-aws)

## Save your current cluster credentials

For security reasons, charm credentials are not stored inside backups. So, if you plan to restore to a backup at any point in the future, **you will need the `operator`, `replication`, and `rewind` user passwords for your existing cluster**. <!--TODO: just these users? -->

See: [Juju | How to view secrets](https://documentation.ubuntu.com/juju/latest/howto/manage-secrets/#view-all-the-available-secrets)

## Create a backup

Once you have a three-node cluster with configurations set for S3 storage, check that Charmed PostgreSQL is `active` and `idle` with `juju status`. 

Once Charmed PostgreSQL is `active` and `idle`, you can create your first backup with the `create-backup` command:

```shell
juju run postgresql/leader create-backup
```

By default, backups created with the command above will be **full** backups: a copy of *all* your data will be stored in S3. There are 2 other supported types of backups (available in revision 416+):

* Differential: Only modified files since the last full backup will be stored.
* Incremental: Only modified files since the last successful backup (of any type) will be stored.

To specify the desired backup type, use the [`type`](https://charmhub.io/postgresql/actions#create-backup) parameter:

```shell
juju run postgresql/leader create-backup type={full|differential|incremental}
```

To avoid unnecessary service downtime, always use non-primary units for the action `create-backup`. Keep in mind that:

* When TLS is enabled, `create-backup` can only run on replicas (non-primary)
* When TLS is **not** enabled, `create-backup` can only run in the primary unit

## List backups

You can list your available, failed, and in progress backups by running the `list-backups` command:

```shell
juju run postgresql/leader list-backups
```

