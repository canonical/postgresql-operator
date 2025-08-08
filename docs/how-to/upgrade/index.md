# Upgrade (refresh)

A charm **refresh** is any change to the version of a charm, and/or its {term}`workload`. 

Charmed PostgreSQL supports minor in-place {term}`upgrades <upgrade>` via the [`juju refresh`](https://documentation.ubuntu.com/juju/3.6/reference/juju-cli/list-of-juju-cli-commands/refresh/#details) command.

## Supported refresh types

This charm **can only be upgraded to a higher version**. It cannot be {term}`downgraded <downgrade>` to a lower version.

The charm version can only be lowered in the case of an ongoing refresh that needs to {term}`roll back <rollback>` to its {term}`original version` due to a failure.

```{seealso}
* [How to perform a minor upgrade](/how-to/upgrade/minor-upgrade)
* [How to perform a minor rollback](/how-to/upgrade/minor-rollback)
```

## Supported versions

This charm **only supports minor version upgrades**, e.g. 16.9 --> 16.10.

[Contact us](/reference/contacts) if you want to migrate from Charmed PostgreSQL 14 to 16

```{seealso}
* [Charmed PostgreSQL 16 versions](/reference/releases)
```

```{toctree}
:titlesonly:
:hidden:

Perform a minor upgrade <minor-upgrade>
Perform a minor rollback <minor-rollback>
```