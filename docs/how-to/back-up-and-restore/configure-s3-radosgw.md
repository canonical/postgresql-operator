# Configure S3 for RadosGW

A PostgreSQL backup can be stored on any S3-compatible storage. S3 access and configurations are managed with the [s3-integrator charm](https://charmhub.io/s3-integrator).

This guide will teach you how to deploy and configure the s3-integrator charm on Ceph via [RadosGW](https://docs.ceph.com/en/quincy/man/8/radosgw/), send the configuration to a Charmed PostgreSQL application, and update it. 

```{seealso}
[](/how-to/back-up-and-restore/configure-s3-aws)
```

```{caution}
The Charmed PostgreSQL backup tool [pgBackRest](https://pgbackrest.org/) can currently only interact with S3-compatible storages if they work with [SSL/TLS](https://github.com/pgbackrest/pgbackrest/issues/2340).

Backup via plain HTTP is currently not supported.
```

## Configure `s3-integrator`

First, install the MinIO client and create a bucket:

```text
mc config host add dest https://radosgw.mycompany.fqdn <access-key> <secret-key> --api S3v4 --lookup path
mc mb dest/backups-bucket
```

Then, deploy and run the charm:

```text
juju deploy s3-integrator
juju run s3-integrator/leader sync-s3-credentials access-key=<access-key> secret-key=<secret-key> 
```

Lastly, use `juju config` to add your configuration parameters. For example:

```text
juju config s3-integrator \
    endpoint="https://radosgw.mycompany.fqdn" \
    bucket="backups-bucket" \
    path="/postgresql" \
    region="" \
    s3-api-version="" \
    s3-uri-style="path" \
    tls-ca-chain="$(base64 -w0 /path-to-your-server-ca-file)"
```

## Integrate with `postgresql`

To pass these configurations to Charmed PostgreSQL, integrate the two applications:

```text
juju integrate s3-integrator postgresql
```

You can create, list, and restore backups now:

```text
juju run postgresql/leader list-backups 
juju run postgresql/leader create-backup 
juju run postgresql/leader list-backups 
juju run postgresql/leader restore backup-id=<backup-id-here> 
```

You can also update your S3 configuration options after integrating using
```text
juju config s3-integrator <option>=<value>
```

The s3-integrator charm [accepts many configurations](https://charmhub.io/s3-integrator/configure) - enter whatever configurations are necessary for your S3 storage.

