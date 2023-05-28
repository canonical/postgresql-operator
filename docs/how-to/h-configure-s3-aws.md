Charmed PostgreSQL backup can be stored on any S3 compatible storage. The S3 access and configurations are managed with the [s3-integrator charm](https://charmhub.io/s3-integrator). Deploy and configure the s3-integrator charm for **[AWS S3](https://aws.amazon.com/s3/)** (click [here](/t/charmed-postgresql-how-to-configure-s3-for-radosgw/10313) to backup on Ceph via RadosGW):
```shell
juju deploy s3-integrator
juju run-action s3-integrator/leader sync-s3-credentials access-key=<access-key-here> secret-key=<secret-key-here> --wait
juju config s3-integrator \
    endpoint="https://s3.amazonaws.com" \
    bucket="postgresql-test-bucket-1" \
    path="/postgresql-test" \
    region="us-west-2"
```

To pass these configurations to Charmed PostgreSQL, relate the two applications:
```shell
juju relate s3-integrator postgresql
```

You can create/list/restore backups now:

```shell
juju run-action postgresql/leader list-backups --wait
juju run-action postgresql/leader create-backup --wait
juju run-action postgresql/leader list-backups --wait
juju run-action postgresql/leader restore backup-id=<backup-id-here> --wait
```

You can also update your S3 configuration options after relating, using:
```shell
juju config s3-integrator <option>=<value>
```

The s3-integrator charm [accepts many configurations](https://charmhub.io/s3-integrator/configure) - enter whatever configurations are necessary for your S3 storage.