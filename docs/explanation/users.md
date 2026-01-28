(users)=
# Users

There are three types of users in PostgreSQL:

* Internal users (used by the charmed operator)
* Relation users (used by related applications)
  * Extra user roles (if default permissions are not enough)
* Identity users (used when LDAP is enabled)

## Internal users

The operator uses the following internal DB users:

* `postgres` - the [initial/default](/how-to/manage-passwords) PostgreSQL user. Used for very initial bootstrap only.
* `operator` - the user that `charm.py` uses to manage database/cluster.
* `replication` - the user performs replication between database PostgreSQL cluster members.
* `rewind` - the internal user for synchronising a PostgreSQL cluster with another copy of the same cluster.
* `monitoring` - the user for [COS integration](/how-to/monitoring-cos/enable-monitoring).
* `backups` - the user to [perform/list/restore backups](/how-to/back-up-and-restore/create-a-backup).

The full list of internal users is available in charm [source code](https://github.com/canonical/postgresql-operator/blob/main/src/constants.py). The full dump of internal users (on the newly installed charm):

```text
postgres=# \du
                                      List of roles
  Role name  |                         Attributes                         |  Member of   
-------------+------------------------------------------------------------+--------------
 backup      | Superuser                                                  | {}
 monitoring  |                                                            | {pg_monitor}
 operator    | Superuser, Create role, Create DB, Replication, Bypass RLS | {}
 postgres    | Superuser                                                  | {}
 replication | Replication                                                | {}
 rewind      |                                                            | {}
```

```{note}
It is forbidden to use/manage described above users, as they are dedicated to the operator's logic.

Use the [data-integrator](https://charmhub.io/data-integrator) charm to generate, manage, and remove external credentials.
```

Passwords for internal users can be managed using Juju secrets. See {ref}`manage-passwords`.

## Relation users

The operator created a dedicated user for every application related/integrated with database. Those users are removed on the juju relation/integration removal request. However, DB data stays in place and can be reused on re-created relations (using new user credentials):

```text
postgres=# \du
                                      List of roles
  Role name  |                         Attributes                         |  Member of   
-------------+------------------------------------------------------------+--------------
 ..
 relation-6  |                                                            | {}
 relation-8  |                                                            | {}
 ...
```

If password rotation is required for users used in relations, the relation must be removed and created again:

```text
> juju remove-relation postgresql myclientapp
> juju wait-for application postgresql
> juju relate postgresql myclientapp
```


### Extra user roles

When an application charm requests a new user through the relation/integration, it can specify that the user should be part of a predefined role to give them additional permissions. Please check [](/explanation/roles) for the list of available roles.

## Identity users

The operator considers Identity users all those that are automatically created when the LDAP integration is enabled, or in other words, the [GLAuth](https://charmhub.io/glauth-k8s) charm is related/integrated.

When synchronised from the LDAP server, these users do not have any permissions by default, so the LDAP group they belonged to must be mapped to a PostgreSQL pre-defined authorisation role by using the `ldap-map` configuration option.

