# Configure S3 for AWS

A Charmed PostgreSQL backup can be stored on any S3-compatible storage. S3 access and configurations are managed with the [s3-integrator charm](https://charmhub.io/s3-integrator).

This guide will teach you how to deploy and configure the s3-integrator charm for [AWS S3](https://aws.amazon.com/s3/), send the configurations to the Charmed PostgreSQL application, and update it. (To configure S3 for RadosGW, see [this guide](/how-to/back-up-and-restore/configure-s3-radosgw))

(configure-s3-integrator-aws)=
## Configure s3-integrator

First, deploy and run the charm:
```text
juju deploy s3-integrator
juju run s3-integrator/leader sync-s3-credentials access-key=<access-key-here> secret-key=<secret-key-here>
```
Then, use `juju config` to add your configuration parameters. For example:
```text
juju config s3-integrator \
    endpoint="https://s3.us-west-2.amazonaws.com" \
    bucket="postgresql-test-bucket-1" \
    path="/postgresql-test" \
    region="us-west-2"
```
```{note} 
There is now an experimental configuration option that sets up a retention time (in days) for backups stored in S3:  [`experimental-delete-older-than-days`](https://charmhub.io/s3-integrator/configuration?channel=latest/edge#experimental-delete-older-than-days). More info on [this guide](/how-to/back-up-and-restore/manage-backup-retention)
```

```{note} 
The amazon S3 endpoint must be specified as `s3.<region>.amazonaws.com ` within the first 24 hours of creating the bucket. For older buckets, the endpoint `s3.amazonaws.com` can be used.

See [this post](https://repost.aws/knowledge-center/s3-http-307-response) for more information. 
```

## Integrate with Charmed PostgreSQL

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
You can also update your S3 configuration options after relating using:
```text
juju config s3-integrator <option>=<value>
```

The s3-integrator charm accepts many [configurations](https://charmhub.io/s3-integrator/configure) - enter whichever are necessary for your S3 storage.

