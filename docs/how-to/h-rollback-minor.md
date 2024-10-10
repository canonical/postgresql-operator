[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# Perform a minor rollback
**Example**: PostgreSQL 14.9 -> PostgreSQL 14.8<br/>
(including simple charm revision bump: from revision 43 to revision 42)

After a `juju refresh`, if there are any version incompatibilities in charm revisions, its dependencies, or any other unexpected failure in the upgrade process, the process will be halted and enter a failure state.

Even if the underlying PostgreSQL cluster continues to work, it’s important to roll back the charm to 
a previous revision so that an update can be attempted after further inspection of the failure.

[note type="caution"]
**Warning:** Do NOT trigger `rollback` during the running `upgrade` action! It may cause an unpredictable PostgreSQL cluster state!
[/note]

## Summary
1. **Prepare** the Charmed PostgreSQL VM application for the in-place rollback. 
2. **Rollback**. Once started, all units in a cluster will be executed sequentially. The rollback will be aborted (paused) if the unit rollback has failed.
3. **Check**. Make sure the charm and cluster are in a healthy state again.

## Step 1: Prepare

To execute a rollback, we use a similar procedure to the upgrade. The difference is the charm revision to upgrade to. In this guide's example, we will refresh the charm back to revision `182`.

It is necessary to re-run `pre-upgrade-check` action on the leader unit, to enter the upgrade recovery state:
```shell
juju run postgresql/leader pre-upgrade-check
```

## Step 2: Rollback
When using a charm from charmhub:

```shell
juju refresh postgresql --revision=182
```

When deploying from a local charm file, one must have the previous revision charm file and run:

```
juju refresh postgresql --path=./postgresql_ubuntu-22.04-amd64.charm
```

Where `postgresql_ubuntu-22.04-amd64.charm` is the previous revision charm file.

The first unit will be rolled out and should rejoin the cluster after settling down. After the refresh command, the juju controller revision for the application will be back in sync with the running Charmed PostgreSQL revision.

## Step 3: Check

Future [improvements are planned](https://warthogs.atlassian.net/browse/DPE-2621) to check the state on pods/clusters on a low level. At the moment check `juju status` to make sure the cluster [state](/t/10844) is OK.