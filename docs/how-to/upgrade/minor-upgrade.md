# Perform a minor upgrade

A minor upgrade is a {term}`refresh` from one workload version to a higher one within the same major version. E.g. PostgreSQL 14.12 --> PostgreSQL 14.15.

The refresh process boils down to:
* Running a pre-refresh check to ensure your deployment can undergo a safe upgrade process
* Running the refresh command to initiate the process
* Monitoring and following UI instructions

Once in the refresh is in progress, the UI will clearly indicate what is happening to each unit, and what actions are required from the user to continue the process or roll back in case of a problem.

```{seealso}
* [All Charmed PostgreSQL minor versions](/reference/releases)
* [How to perform a minor rollback](/how-to/upgrade/minor-rollback)
* [](/how-to/back-up-and-restore/create-a-backup)
* [Developer docs for charm-refresh](https://canonical-charm-refresh.readthedocs-hosted.com/latest/)
    * [`pause_after_unit_refresh` documentation](https://canonical-charm-refresh.readthedocs-hosted.com/latest/user-experience/config/#pause-after-unit-refresh)
```

---

## Precautions

Below are some strongly recommended precautions before refreshing to ensure minimal service disruption:

**Make sure to have a [backup](/how-to/back-up-and-restore/create-a-backup) of your data.**

**Do not perform operations that modify your cluster while refreshing.**

Concurrency with other operations is not supported, and it can lead the cluster to inconsistent states.

```{dropdown} Examples
Avoid operations such as (but not limited to) the following:

* Adding or removing units
* Creating or destroying new relations
* Changes in workload configuration
* Upgrading other connected/related/integrated applications simultaneously
```

**Integrate Charmed PostgreSQL with the [Charmed PgBouncer](https://charmhub.io/pgbouncer) operator.** 

This will ensure minimal service disruption, if any.


## Pre-refresh check

The `pre-refresh-check` action will check that a refresh is possible and will switch the primary, if necessary, for a safe upgrade process. 

Run a pre-refresh check against the leader unit:

```shell
juju run postgresql/leader pre-refresh-check
```

Do not refresh if there are errors in the output.

```{tip}
Copy down the rollback command from the action output in case a rollback is needed later.
```

## Start refresh

The following command will refresh the charm to the latest version in the channel you originally deployed your application from, e.g. `16/stable`:

```shell
juju refresh postgresql
```

To refresh your charm to the latest version of a specific channel or track, use the `--channel` flag. For example:

```shell
juju refresh postgresql --channel 16/edge
```

Units will be refreshed one by one based on role: first the `replica` units, then `sync-standby`, and lastly the `leader` unit. 

If there are any version incompatibilities in charm revisions, dependencies, or any other unexpected failure in the upgrade process, the process will halt and enter a failure state.

```{attention}
Only trigger a rollback if the refresh has expicitly failed and cannot continue. <!--TODO: examples-->
```

## Resume refresh 

If the [`pause_after_unit_refresh`](https://charmhub.io/postgresql/configurations?channel=16/edge#pause_after_unit_refresh) config option on your PostgreSQL application is set to `first` (default) or `all`, you'll need to monitor and manually resume the refresh when one or more units have finished refreshing individually.

When the refresh pauses and all units are in an idle state, check that they are healthy. <!-- TODO: how? -->

Then, run the `resume-refresh` action on the unit shown by the app status.

<!--TODO: example -->
