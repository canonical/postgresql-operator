(charm-versions)=
# PostgreSQL major versions

Charmed PostgreSQL is available in multiple versions to support different deployment requirements and lifecycle stages. It is shipped in the following [tracks](https://documentation.ubuntu.com/juju/3.6/reference/charm/#track):


| Charm name              | Charmhub channel | Type   | Status                                                            |
| ----------------------- | ---------------- | ------ | ----------------------------------------------------------------- |
| PostgreSQL 16           | `16/stable`      | modern | ![check] Latest version - new features are released here          |
| PostgreSQL 14           | `14/stable`      | modern | ![check] In maintenance mode - bug fixes and security updates only |
| Legacy PostgreSQL charm | `latest/stable`  | legacy | ![cross] Deprecated                                               |

## Legacy vs. modern

There are two [generations](https://documentation.ubuntu.com/juju/3.6/reference/charm/#by-generation) of charms stored under the same charm name `postgresql`. In these docs, we refer to them as "legacy" and "modern". 

Legacy charm (deprecated)
: Also known as a [Reactive charm](https://documentation.ubuntu.com/juju/3.6/reference/charm/#reactive-charm). Found in the Charmhub channel `latest/stable`.
: Provided `db` and `db-admin` endpoints for the `pgsql` interface.

Modern charm
: Also known as an [Ops charm](https://documentation.ubuntu.com/juju/3.6/reference/charm/#ops-charm). Found in the Charmhub channels `14/stable` and `16/stable`.
: `14/stable` provides legacy endpoints and new `database` endpoint for the `postgresql_client` interface.
: `16/stable` **does not** provide legacy endpoints - only the new `database` `database` endpoint for the `postgresql_client` interface.

```{seealso}
* [](/explanation/charm-versions/legacy-charm)
* [](/explanation/charm-versions/modern-charm)
* [](/explanation/interfaces-and-endpoints)
```

<!--Links-->

[cross]: https://img.icons8.com/?size=16&id=CKkTANal1fTY&format=png&color=D00303
[check]: https://img.icons8.com/color/20/checkmark--v1.png

```{toctree}
:titlesonly:
:hidden:

Legacy charm <legacy-charm>
Modern charm <modern-charm>
```