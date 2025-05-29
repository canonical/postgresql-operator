# Upgrade

For instructions on carrying out **minor version upgrades**, see the following guides:

* [Minor upgrade](/how-to/upgrade/perform-a-minor-upgrade), e.g. PostgreSQL 14.8 -> PostgreSQL 14.9<br/>
(including charm revision bump 42 -> 43).
* [Minor rollback](/how-to/upgrade/perform-a-minor-rollback), e.g. PostgreSQL 14.9 -> PostgreSQL 14.8<br/>
(including charm revision return 43 -> 42).

This charm does not support in-place upgrades for major version changes. 

```{note}
We will soon publish a migration guide with instructions on how to change from PostgreSQL 14 to 16.
```

```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Perform a minor upgrade <perform-a-minor-upgrade>
Perform a minor rollback <perform-a-minor-rollback>
