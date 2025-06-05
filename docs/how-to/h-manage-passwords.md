# How to manage passwords

If you are using **PostgreSQL 16** (channel `16/<any>`), passwords are managed with Juju secrets.
> See the section [Passwords with PostgreSQL 16](#passwords-with-postgresql-16).

If you are using **PostgreSQL 14** (channel `14/<any>`), passwords are managed with Juju's `get-password` and `set-password` actions.
> See the section [Passwords with PostgreSQL 14](#passwords-with-postgresql-14).

## Passwords with PostgreSQL 16

On PostgreSQL 16, the charm uses [Juju secrets](https://documentation.ubuntu.com/juju/latest/reference/secret/#secret) to manage passwords.

See also: [Juju | How to manage secrets](https://documentation.ubuntu.com/juju/latest/howto/manage-secrets/#manage-secrets)

### Create a secret
To create a secret in Juju containing one or more user passwords:
```
juju add-secret <secret_name> <user_a>=<password_a> <user_b>=<password_b>
```

The command above will output a secret URI, which you'll need for configuring `system-users`.

Admin users that were not included in the secret will use an automatically created password.

To grant the secret to the `postgresql` charm:
```
juju grant-secret <secret_name> postgresql
```

### Configure `system-users`
To set the `system-users` config option to the secret URI:
```
juju config charm-app system-users=<secret_URI>
```

When the `system-users` config option is set, the charm will:
* Use the content of the secret specified by the `system-users` config option instead of the one generated.
* Update the passwords of the internal `system-users` in its user database.

If the config option is **not** specified, the charm will automatically generate passwords for the internal system-users and store them in a secret.

To retrieve the password of an internal system-user, run the `juju show-secret` command with the respective secret URI.

### Update a secret
To update an existing secret:
```
juju update-secret <secret_name> <user_a>=<new_password_a> <user_c>=<password_c>
```
In this example,
* `user_a`'s password was updated from `password_a` to `new_password_a`
* `user_c`'s password was updated from an auto-generated password to `password_c`
* `user_b`'s password remains as it was when the secret was added, but **`user_b` is no longer part of the secret**.

See also: [Explanation > Users](/t/10798)

## Passwords with PostgreSQL 14

### Get password
To retrieve the operator's password:
```
juju run postgresql/leader get-password
```
### Set password
To change the operator's password to a new, randomized password:
```
juju run postgresql/leader set-password
```

To set a manual password for the operator/admin user:
```
juju run postgresql/leader set-password password=<password>
```

To set a manual password for another user:
```
juju run postgresql/leader set-password username=<username> password=<password>
```