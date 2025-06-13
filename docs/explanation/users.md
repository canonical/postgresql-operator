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
* `rewind` - the internal user for synchronizing a PostgreSQL cluster with another copy of the same cluster.
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
<!-- TODO: check if this can be replaced with secrets 
Passwords for *internal* users can be rotated using the action 'set-password':

```text
> juju show-action postgresql set-password
Change the system user's password, which is used by charm. It is for internal charm users and SHOULD NOT be used by applications.

Arguments
password:
  type: string
  description: The password will be auto-generated if this option is not specified.
username:
  type: string
  description: The username, the default value 'operator'. Possible values - operator, replication, rewind.
```

For example, to generate a new random password for *internal* user:


```text
> juju run-action --wait postgresql/leader set-password username=operator

unit-postgresql-1:
  UnitId: postgresql/1
  id: "2"
  results:
    password: k4qqnWSZJZrcMt4B
  status: completed
```
To set a predefined password for the specific user, run:
```text
> juju run-action --wait postgresql/leader set-password username=operator password=newpassword

unit-postgresql-1:
  UnitId: postgresql/1
  id: "4"
  results:
    password: newpassword
  status: completed
```

The action `set-password` must be executed on juju leader unit (to update peer relation data with new value).

-->

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

When an application charm requests a new user through the relation/integration it can specify that the user should have the `admin` role in the `extra-user-roles` field. The `admin` role enables the new user to read and write to all databases (for the `postgres` system database it can only read data) and also to create and delete non-system databases.

```{note}
`extra-user-roles` is only supported by the modern interface `postgresql_client`. It is not supported for the legacy `pgsql` interface. R

Read more about the supported charm interfaces in [](/explanation/interfaces-and-endpoints).
```

## Identity users

The operator considers Identity users all those that are automatically created when the LDAP integration is enabled, or in other words, the [GLAuth](https://charmhub.io/glauth-k8s) charm is related/integrated.

When synchronized from the LDAP server, these users do not have any permissions by default, so the LDAP group they belonged to must be mapped to a PostgreSQL pre-defined authorization role by using the `ldap_map` configuration option.

