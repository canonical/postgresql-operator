# How to manage passwords

In Charmed PostgreSQL 14, user credentials are managed with Juju's `get-password` and `set-password` actions.

## Get password

To retrieve the operator's password:

```text
juju run postgresql/leader get-password
```

## Set password

To change the operator's password to a new, randomised password:

```text
juju run postgresql/leader set-password
```

To set a manual password for the operator/admin user:

```text
juju run postgresql/leader set-password password=<password>
```

To set a manual password for another user:

```text
juju run postgresql/leader set-password username=<username> password=<password>
```

Where `<username>` can be any of the system users: `operator`, `replication`, `rewind`, `monitoring`, `backup`, or `ldap`.

