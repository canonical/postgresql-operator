# Charm Users explanations

There are three types of users in PostgreSQL:
* Internal users (used by charm operator)
* Relation users (used by related applications)
  * Extra user roles (if default permissions are not enough)
* Identity users (used when LDAP is enabled)

<a name="internal-users"></a>
## Internal users explanations:

The operator uses the following internal DB users:

* `postgres` - the [initial/default](https://charmhub.io/postgresql/docs/t-manage-passwords) PostgreSQL user. Used for very initial bootstrap only.
* `operator` - the user that charm.py uses to manage database/cluster.
* `replication` - the user performs replication between database PostgreSQL cluster members.
* `rewind` - the internal user for synchronizing a PostgreSQL cluster with another copy of the same cluster.
* `monitoring` - the user for [COS integration](https://charmhub.io/postgresql/docs/h-enable-monitoring).
* `backups` - the user to [perform/list/restore backups](https://charmhub.io/postgresql/docs/h-create-and-list-backups).

The full list of internal users is available in charm [source code](https://github.com/canonical/postgresql-operator/blob/main/src/constants.py). The full dump of internal users (on the newly installed charm):

```shell
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
**Note**: it is forbidden to use/manage described above users! They are dedicated to the operators logic! Please use [data-integrator](https://charmhub.io/postgresql/docs/t-integrations) charm to generate/manage/remove an external credentials.

It is allowed to rotate passwords for *internal* users using action 'set-password':
```shell
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

```shell
> juju run-action --wait postgresql/leader set-password username=operator

unit-postgresql-1:
  UnitId: postgresql/1
  id: "2"
  results:
    password: k4qqnWSZJZrcMt4B
  status: completed
```
To set a predefined password for the specific user, run:
```shell
> juju run-action --wait postgresql/leader set-password username=operator password=newpassword

unit-postgresql-1:
  UnitId: postgresql/1
  id: "4"
  results:
    password: newpassword
  status: completed
```
**Note**: the action `set-password` must be executed on juju leader unit (to update peer relation data with new value).

<a name="relation-users"></a>
## Relation users explanations:

The operator created a dedicated user for every application related/integrated with database. Those users are removed on the juju relation/integration removal request. However, DB data stays in place and can be reused on re-created relations (using new user credentials):

```shell
postgres=# \du
                                      List of roles
  Role name  |                         Attributes                         |  Member of   
-------------+------------------------------------------------------------+--------------
 ..
 relation-6  |                                                            | {}
 relation-8  |                                                            | {}
 ...
```

**Note**: If password rotation is needed for users used in relations, it is needed to remove the relation and create it again:
```shell
> juju remove-relation postgresql myclientapp
> juju wait-for application postgresql
> juju relate postgresql myclientapp
```

<a name="extra-user-roles"></a>
### Extra user roles

When an application charm requests a new user through the relation/integration it can specify that the user should have the `admin` role in the `extra-user-roles` field. The `admin` role enables the new user to read and write to all databases (for the `postgres` system database it can only read data) and also to create and delete non-system databases.

**Note**: `extra-user-roles` is supported by modern interface `postgresql_client` only and missing for legacy `pgsql` interface. Read more about the supported charm interfaces [here](/t/10251).

<a name="identity-users"></a>
## Identity users explanations:
The operator considers Identity users all those that are automatically created when the LDAP integration is enabled, or in other words, the [GLAuth](https://charmhub.io/glauth-k8s) charm is related/integrated.

When synchronized from the LDAP server, these users do not have any permissions by default, so the LDAP group they belonged to must be mapped to a PostgreSQL pre-defined authorization role by using the `ldap_map` configuration option.