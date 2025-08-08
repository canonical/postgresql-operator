# Upgrade (refresh)

Charmed PostgreSQL supports minor in-place {term}`upgrades <upgrade>` via the [`juju refresh`](https://documentation.ubuntu.com/juju/3.6/reference/juju-cli/list-of-juju-cli-commands/refresh/#details) command:

```{admonition} Please keep in mind: 
:class: caution

* This charm **can only be upgraded to a higher version**. It cannot be {term}`downgraded <downgrade>` to a lower version.
* This charm **only supports minor version upgrades**, e.g. 16.9 --> 16.10.
* If anything goes wrong during the refresh process, the best option is usually to perorm a {term}`rollback`.
```

Guides:

```{toctree}
:titlesonly:

Perform a minor upgrade <minor-upgrade>
Perform a minor rollback <minor-rollback>
```

## Glossary

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
```