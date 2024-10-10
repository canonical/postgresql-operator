[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# Perform a minor upgrade

**Example**: PostgreSQL 14.8 -> PostgreSQL 14.9<br/>
(including simple charm revision bump: from revision 193 to revision 196).

This guide is part of [Charmed PostgreSQL Upgrades](/t/12086). Please refer to this page for more information and an overview of the content.

## Summary
- [**Pre-upgrade checks**](#pre-upgrade-checks): Important information to consider before starting an upgrade.
- [**1. Collect**](#step-1-collect) all necessary pre-upgrade information. It will be necessary for a rollback, if needed. **Do not skip this step**; better to be safe than sorry!
- [**2. Prepare**](#step-2-prepare) your Charmed PostgreSQL Juju application for the in-place upgrade. See the step details for all technical details executed by charm here.
- [**3. Upgrade**](#step-3-upgrade). Once started, all units in a cluster will be executed sequentially. The upgrade will be aborted (paused) if the unit upgrade has failed.
- [**4. (Optional) Consider a rollback**](#step-4-rollback-optional) in case of disaster. 
    - Please [inform us](/t/11863) about your case scenario troubleshooting to trace the source of the issue and prevent it in the future.
- [**Post-upgrade check**](#step-5-post-upgrade-check). Make sure all units are in the proper state and the cluster is healthy.

---

## Pre-upgrade checks
Before performing a minor PostgreSQL upgrade, there are some important considerations to take into account:
* Concurrency with other operations during the upgrade
* Backing up your data
* Service disruption

### Concurrency with other operations
**We strongly recommend to NOT perform any other extraordinary operations on Charmed PostgreSQL cluster while upgrading.** 

Some examples are operations like (but not limited to) the following:

* Adding or removing units
* Creating or destroying new relations
* Changes in workload configuration
* Upgrading other connected/related/integrated applications simultaneously

Concurrency with other operations is not supported, and it can lead the cluster into inconsistent states.
### Backups
**Make sure to have a backup of your data when running any type of upgrade.**

Guides on how to configure backups with S3-compatible storage can be found [here](/t/9683).

### Service disruption
**It is recommended to deploy your application in conjunction with the [Charmed PgBouncer](https://charmhub.io/pgbouncer) operator.** 

This will ensure minimal service disruption, if any.

## Step 1: Collect

[note]
This step is only valid when deploying from [charmhub](https://charmhub.io/). 

If a [local charm](https://juju.is/docs/sdk/deploy-a-charm) is deployed (revision is small, e.g. 0-10), make sure the proper/current local revision of the `.charm` file is available BEFORE going further. You might need it for a rollback.
[/note]

The first step is to record the revision of the running application as a safety measure for a rollback action. To accomplish this, simply run the `juju status` command and look for the deployed Charmed PostgreSQL revision in the command output, e.g.:

```shell
Model        Controller  Cloud/Region         Version  SLA          Timestamp
welcome-lxd  lxd         localhost/localhost  3.1.6    unsupported  11:35:36+02:00

App         Version  Status  Scale  Charm       Channel       Rev  Exposed  Message
postgresql  14.9     active      3  postgresql  14/candidate  330  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/3*  active    idle   3        10.3.217.74     5432/tcp  
postgresql/4   active    idle   4        10.3.217.95     5432/tcp  
postgresql/5   active    idle   5        10.3.217.108    5432/tcp  

Machine  State    Address       Inst id        Base          AZ  Message
3        started  10.3.217.74   juju-d483b7-3  ubuntu@22.04      Running
4        started  10.3.217.95   juju-d483b7-4  ubuntu@22.04      Running
5        started  10.3.217.108  juju-d483b7-5  ubuntu@22.04      Running
```

In this example, the current revision is `330`. Store it safely to use in case of a rollback!

## Step 2: Prepare

Before running the [`juju refresh`](https://juju.is/docs/juju/juju-refresh) command, it’s necessary to run the `pre-upgrade-check` action against the leader unit:

```shell
juju run postgresql/leader pre-upgrade-check
```
Make sure there are no errors in the result output.

This action will configure the charm to minimize the amount of primary switchover, among other preparations for a safe upgrade process. After successful execution, the charm is ready to be upgraded.

## Step 3: Upgrade

Use the  `juju refresh` command to trigger the charm upgrade process.

Example with channel selection:
```shell
juju refresh postgresql --channel 14/edge
```
Example with specific revision selection:
```shell
juju refresh postgresql --revision=342
```
Example with a local charm file:
```shell
juju refresh postgresql --path ./postgresql_ubuntu-22.04-amd64.charm
```

All units will be refreshed (i.e. receive new charm content), and the upgrade will execute one unit at a time. 

[note]
**Note:** To reduce connection disruptions, the order in which the units are upgraded is based on roles: 

First the `replica` units, then the `sync-standby` units, and lastly, the `leader`(or `primary`) unit. 
[/note]

 `juju status` will look like:

```shell
Model        Controller  Cloud/Region         Version  SLA          Timestamp
welcome-lxd  lxd         localhost/localhost  3.1.6    unsupported  11:36:18+02:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql  14.9     active      3  postgresql  14/edge  331  no       

Unit           Workload  Agent      Machine  Public address  Ports     Message
postgresql/3*  waiting   idle       3        10.3.217.74     5432/tcp  other units upgrading first...
postgresql/4   waiting   idle       4        10.3.217.95     5432/tcp  other units upgrading first...
postgresql/5   waiting   executing  5        10.3.217.108    5432/tcp  waiting for database initialisation

Machine  State    Address       Inst id        Base          AZ  Message
3        started  10.3.217.74   juju-d483b7-3  ubuntu@22.04      Running
4        started  10.3.217.95   juju-d483b7-4  ubuntu@22.04      Running
5        started  10.3.217.108  juju-d483b7-5  ubuntu@22.04      Running
```

After each unit completes the upgrade, the message will go blank, and a next unit will follow:

```shell
Model        Controller  Cloud/Region         Version  SLA          Timestamp
welcome-lxd  lxd         localhost/localhost  3.1.6    unsupported  11:36:31+02:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql  14.9     active      3  postgresql  14/edge  331  no       

Unit           Workload     Agent      Machine  Public address  Ports     Message
postgresql/3*  waiting      idle       3        10.3.217.74     5432/tcp  other units upgrading first...
postgresql/4   maintenance  executing  4        10.3.217.95     5432/tcp  refreshing the snap
postgresql/5   active       idle       5        10.3.217.108    5432/tcp  

Machine  State    Address       Inst id        Base          AZ  Message
3        started  10.3.217.74   juju-d483b7-3  ubuntu@22.04      Running
4        started  10.3.217.95   juju-d483b7-4  ubuntu@22.04      Running
5        started  10.3.217.108  juju-d483b7-5  ubuntu@22.04      Running
```
### Important Notes
**Do NOT trigger `rollback` procedure during the running `upgrade` procedure.**
It is expected to have some status changes during the process: `waiting`, `maintenance`, `active`. 

Make sure `upgrade` has failed/stopped and cannot be fixed/continued before triggering `rollback`!

**Please be patient during huge installations.**
Each unit should recover shortly after the upgrade, but time can vary depending on the amount of data written to the cluster while the unit was not part of it. 

**Incompatible charm revisions or dependencies will halt the process.**
After a `juju refresh`, if there are any version incompatibilities in charm revisions, its dependencies, or any other unexpected failure in the upgrade process, the upgrade process will be halted and enter a failure state.

## Step 4: Rollback (optional)

The step must be skipped if the upgrade went well! 

Although the underlying PostgreSQL Cluster continues to work, it’s important to roll back the charm to a previous revision so that an update can be attempted after further inspection of the failure. Please switch to the dedicated [minor rollback](/t/12090) tutorial if necessary.

## Post-upgrade check

Future [improvements are planned](https://warthogs.atlassian.net/browse/DPE-2621) to check the state of a pod/cluster on a low level. 

For now, use `juju status` to make sure the cluster [state](/t/10844) is OK.

<!---
**TODOs:**

* Clearly describe "failure state"!!!
* How to check progress of upgrade (is it failed or running?)?
* Hints how to fix failed upgrade? mysql-shell hints....
* Describe pre-upgrade check: free space, etc.
* Hint to add extra unit to upgrade first keeping cluster "safe".
--->