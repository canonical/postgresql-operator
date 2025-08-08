# Perform a minor rollback

After running `juju refresh`, if there are any version incompatibilities in charm revisions, its dependencies, or any other unexpected failure in the upgrade process, the process will be halted and enter a failure state.

Even if the underlying PostgreSQL cluster continues to work, it’s important to roll back the charm to the {term}`original version` so that an update can be attempted after further inspection of the failure.

```{attention}
Only trigger a rollback if the refresh has expicitly failed and cannot continue. Do not initiate a rollback while the refresh process is still running.

<!--TODO: examples-->
```

---

## Initiate rollback

Perform a rollback with the command obtained from the [pre-refresh check](pre-refresh check) that was performed before initiating the refresh process.

<!--TODO: example-->

## Resume rollback

If the [`pause_after_unit_refresh`](https://charmhub.io/postgresql/configurations?channel=16/edge#pause_after_unit_refresh) config option on your PostgreSQL application is set to `first` (default) or `all`, you'll need to monitor and manually resume the refresh when one or more units have finished refreshing individually.

When the refresh pauses and all units are in an idle state, check that they are healthy. <!-- TODO: how? -->

Then, to resume the rollback, run the `resume-refresh` action on the unit shown by the app status.

<!-->