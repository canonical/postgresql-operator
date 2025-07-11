# Charm statuses

```{caution}
This reference is a work in progress and not fit for production. [Contact us](/reference/contacts) if you are interested in the topic.
```

The charm follows [standard Juju applications statuses](https://documentation.ubuntu.com/juju/3.6/reference/status/#application-status). Here you can find the expected end-users reaction on different statuses:

| Juju Status | Message | Expectations | Actions |
|-------|-------|-------|-------|
| **active** | any | Normal charm operations | No actions required |
| **waiting** | any | Charm is waiting for relations to be finished | No actions required |
| **maintenance** | any | Charm is performing the internal maintenance (e.g. cluster re-configuration, upgrade, ...) | No actions required |
| **blocked** | the S3 repository has backups from another cluster | The bucket contains foreign backup. To avoid accident DB corruption, use clean bucket. The cluster identified by Juju app name + DB UUID. | Choose/change the new S3 [bucket](https://charmhub.io/s3-integrator/configuration#bucket)/[path](https://charmhub.io/s3-integrator/configuration#path) OR clean the current one. |
| **blocked** | failed to update cluster members on member | TODO: error/retry? | |
| **blocked** | failed to install snap packages | There are issues with the network connection and/or the Snap Store | Check your internet connection and https://status.snapcraft.io/. Remove the application and when everything is OK, deploy the charm again |
| **blocked** | failed to patch snap `seccomp` profile | The charm failed to patch one issue that happens when pgBackRest restores a backup (this blocked status should be removed when https://github.com/pgbackrest/pgbackrest/releases/tag/release%2F2.46 is added to the snap) | Remove the unit and add it back again |
| **blocked** | failed to set up postgresql_exporter options | The charm failed to set up the metrics exporter | Remove the unit and add it back again |
| **blocked** | failed to start Patroni | TODO: error/retry? | |
| **blocked** | Failed to create postgres user | The charm couldn't create the default `postgres` database user due to connection problems | Connect to the database using the `operator` user and the password from the `get-password` action, then run `CREATE ROLE postgres WITH LOGIN SUPERUSER;` |
| **blocked** | Failed to restore backup | The database couldn't start after the restore | The charm needs fix in the code to recover from this status and enable a new restore to be requested |
| **blocked** | Please choose one endpoint to use. No need to relate all of them simultaneously! | [The modern / legacy interfaces](/explanation/legacy-charm) should not be used simultaneously. | Remove modern or legacy relation. Choose one to use at a time. |
| **error** | any | An unhanded internal error happened | Read the message hint. Execute `juju resolve <error_unit/0>` after addressing the root of the error state |
| **terminated** | any | The unit is gone and will be cleaned by Juju soon | No actions possible |
| **unknown** | any | Juju doesn't know the charm app/unit status. Possible reason: K8s charm termination in progress. | Manual investigation required if status is permanent |

