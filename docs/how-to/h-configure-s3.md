Charmed PostgreSQL backup can be stored on any S3 compatible storage. The S3 access and configurations are managed with the [s3-integrator charm](https://charmhub.io/s3-integrator). Deploy and configure the s3-integrator charm:
```
juju deploy s3-integrator --channel=edge
juju run-action s3-integrator/leader sync-s3-credentials access-key=<access-key-here> secret-key=<secret-key-here> --wait
juju config s3-integrator \
    path="/postgresql-test" \
    region="us-west-2" \
    bucket="postgresql-test-bucket-1" \
    endpoint="https://s3.amazonaws.com"
```
The s3-integrator charm [accepts many configurations](https://charmhub.io/s3-integrator/configure) - enter whatever configurations are necessary for your s3 storage.

To pass these configurations to Charmed PostgreSQL, relate the two applications:
```
juju relate s3-integrator postgresql
```

You can also update your configuration options after relating:
```
juju config s3-integrator endpoint=<endpoint>
```
