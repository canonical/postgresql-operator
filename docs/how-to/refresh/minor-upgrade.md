# Perform a minor upgrade

A minor in-place upgrade is a refresh from one {term}`workload` version to a higher one, within the same major version (e.g. PostgreSQL 14.12 --> PostgreSQL 14.15)

Once in the refresh is in progress, the UI will clearly indicate what is happening to each unit, and what actions are required from the user to continue the process or roll back in case of a problem.

If your upgrade has failed, see [Roll back an in-progress refresh](/how-to/refresh/rollback)

```{seealso}
[All Charmed PostgreSQL minor versions](/reference/releases)
```

## Precautions

Below are some strongly recommended precautions before refreshing to ensure minimal service disruption:

**Make sure to have a [backup](/how-to/back-up-and-restore/create-a-backup) of your data.**

We recommend testing the integrity of your backup by performing a test [restore](/how-to/back-up-and-restore/restore-a-backup).

**Avoid operations that modify your cluster while refreshing.**

Concurrency with other operations is not supported, and it can lead the cluster to inconsistent states.

```{dropdown} Examples
Avoid operations such as (but not limited to) the following:

* Adding or removing units - unless it is necessary for recovery
* Creating or destroying new relations
* Changes in workload configuration
* Upgrading other connected/related/integrated applications simultaneously

[Contact us](/reference/contacts) if you have questions about performing operations during refresh.
```

**Integrate with [Charmed PgBouncer](https://charmhub.io/pgbouncer).** 

This will ensure minimal service disruption, if any.

(pre-refresh-check)=
## Pre-refresh check

The [`pre-refresh-check`](https://canonical-charm-refresh.readthedocs-hosted.com/latest/user-experience/actions/#pre-refresh-check) action checks that the application is healthy and ready to refresh (e.g. no other operations are running).   

Run a pre-refresh check against the leader unit:

```shell
juju run postgresql/leader pre-refresh-check
```

Do not refresh if there are errors in the output.

```{tip}
Copy down the rollback command from the action output in case a rollback is needed later.
```

## Initiate refresh

The following command will refresh the charm to the latest version in the channel you originally deployed your application from:

```shell
juju refresh postgresql
```

It is expected for the Juju agents to enter a temporary `failed` state. 

Units will then be refreshed one by one based on role:
1. `replica` units
2. `sync-standby`
3. `leader` unit.

If there are any version incompatibilities in charm revisions, dependencies, or any other unexpected failure in the upgrade process, the process will halt and enter a failure state.

## Resume refresh

If the [`pause-after-unit-refresh`](https://charmhub.io/postgresql/configurations?channel=16/edge#pause-after-unit-refresh) config option on your PostgreSQL application is set to `first` (default) or `all`, you'll need to monitor and manually resume the refresh when one or more units have finished refreshing individually.

When the refresh pauses and all units are in an idle state, check that they are healthy. <!-- TODO: how? -->

Then, to resume the upgrade, run the `resume-refresh` action on the unit shown by the app status.

<!--TODO: example -->
