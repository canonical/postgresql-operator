(manage-passwords)=
# How to manage passwords

Charmed PostgreSQL 16 uses [Juju secrets](https://documentation.ubuntu.com/juju/latest/reference/secret/#secret) to manage passwords.

```{seealso}
[Juju | How to manage secrets](https://documentation.ubuntu.com/juju/latest/howto/manage-secrets/#manage-secrets)
```

## Create a secret

To create a secret in Juju containing one or more user passwords:

```shell
juju add-secret <secret_name> <user_a>=<password_a> <user_b>=<password_b>
```

The command above will output a secret URI similar to the example below, which you'll need for configuring `system-users`:

```shell
secret:a2whj30el0fco390bt9f
```

Admin users that were not included in the secret will use an automatically created password.

To grant the secret to the `postgresql` charm:

```shell
juju grant-secret <secret_name> postgresql
```

(configure-system-users)=
## Configure `system-users`

To set the `system-users` config option to the secret URI:

```shell
juju config postgresql system-users=<secret_URI>
```

```{tip}
Note that `<secret_URI>` includes the the scheme (`secret:`).
```

When the `system-users` config option is set, the charm will:
* Use the content of the secret specified by the `system-users` config option instead of the one generated.
* Update the passwords of the internal `system-users` in its user database.

If the config option is **not** specified, the charm will automatically generate passwords for the internal system-users and store them in a secret.

To retrieve the password of an internal system-user, run the `juju show-secret` command with the respective secret URI.

## Update a secret

To update an existing secret:

```shell
juju update-secret <secret_name> <user_a>=<new_password_a> <user_c>=<password_c>
```

In this example,
* `user_a`'s password was updated from `password_a` to `new_password_a`
* `user_c`'s password was updated from an auto-generated password to `password_c`
* `user_b`'s password remains as it was when the secret was added, but **`user_b` is no longer part of the secret**.

See also: {ref}`users`