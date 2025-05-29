# Upgrade

Currently, the charm supports PostgreSQL major version 14 only. Therefore, in-place upgrades/rollbacks are not possible for major versions. 

```{note}
Canonical is not planning to support in-place upgrades for major version change. The new PostgreSQL charm will have to be installed nearby, and the data will be copied from the old to the new installation. After announcing the next PostgreSQL major version support, the appropriate documentation for data migration will be published.
```

For instructions on carrying out **minor version upgrades**, see the following guides:

* [Minor upgrade](/how-to-guides/upgrade/perform-a-minor-upgrade), e.g. PostgreSQL 14.8 -> PostgreSQL 14.9<br/>
(including charm revision bump 42 -> 43).
* [Minor rollback](/how-to-guides/upgrade/perform-a-minor-rollback), e.g. PostgreSQL 14.9 -> PostgreSQL 14.8<br/>
(including charm revision return 43 -> 42).


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Perform a minor upgrade <perform-a-minor-upgrade>
Perform a minor rollback <perform-a-minor-rollback>
