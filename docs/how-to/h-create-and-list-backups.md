Creating and listing backups requires that you:
* [Have a cluster with at least three-nodes deployed](https://discourse.charmhub.io/t/charmed-postgresql-tutorial-managing-units/TODO)
* Access to S3 storage
* [Have configured settings for S3 storage](https://discourse.charmhub.io/t/configuring-settings-for-s3/TODO)

Once you have a three-node cluster that has configurations set for S3 storage, check that Charmed PostgreSQL is `active` and `idle` with `juju status`. Once Charmed PostgreSQL is `active` and `idle`, you can create your first backup with the `create-backup` command:
```
juju run-action postgresql/leader create-backup --wait
```

You can list your available, failed, and in progress backups by running the `list-backups` command:
```
juju run-action postgresql/leader list-backups --wait
```