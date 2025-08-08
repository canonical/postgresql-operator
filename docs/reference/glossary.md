## Glossary

Common terminology in the context of Charmed PostgreSQL.

### Refresh

This is a simplified summary of refresh terminology. 

For a more detailed glossary, see the [charm refresh  developer documentation](https://canonical-charm-refresh.readthedocs-hosted.com/latest/glossary/). 

```{glossary}
refresh
    `juju refresh` to a different workload and/or charm version.

    Note: *rollback*, *upgrade*, and *downgrade* are specific types of refresh.

upgrade
    `juju refresh` to a higher workload and/or charm version.

downgrade
    `juju refresh` to a lower workload and/or charm version.

rollback
    `juju refresh` to the original workload and charm version while a refresh is in progress.

workload
    A software component that the charm operates. E.g. PostgreSQL.

original version
    Workload and/or charm version of all units before initiating a refresh process
```