# Manage backup retention

Charmed PostgreSQL backups can be managed via a retention policy. This retention can be set by the user in the form of a configuration parameter in the charm [`s3-integrator`](https://charmhub.io/s3-integrator) via the config option  [`experimental-delete-older-than-days`](https://charmhub.io/s3-integrator/configuration?channel=latest/edge#experimental-delete-older-than-days).

This guide will teach you how to set this configuration and how it works in managing existing backups.

```{caution}
This is an experimental parameter; use it with caution.
```

## Configure S3-integrator charm
If not done already, deploy and run the charm:
```text
juju deploy s3-integrator
juju run s3-integrator/leader sync-s3-credentials access-key=<access-key-here> secret-key=<secret-key-here>
```
Then, use `juju config` to add the desired retention time in days:
```text
juju config s3-integrator experimental-delete-older-than-days=<number-of-days>
```
To pass these configurations to a Charmed PostgreSQL application, integrate the two applications:
```text
juju integrate s3-integrator postgresql
```
If at any moment it is desired to remove this option, the user can erase this configuration from the charm:
```text
juju config s3-integrator --reset experimental-delete-older-than-days
```

This configuration will be enforced in **every** Charmed PostgreSQL application related to the configured S3-integrator charm

```{note} 
The retention is **not** enforced automatically once a backup is older than the set amount of days: Backups older than the set retention time will only get expired only once a newer backup is created.

This behaviour avoids complete backup deletion if there has been no newer backups created in the charm.
```

The s3-integrator charm accepts many [configurations](https://charmhub.io/s3-integrator/configure) - enter whichever are necessary for your S3 storage.

