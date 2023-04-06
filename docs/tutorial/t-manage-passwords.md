# Manage Passwords

This is part of the [Charmed PostgreSQL Tutorial](TODO). Please refer to this page for more information and the overview of the content.

## Passwords
When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time it is a good practice to change the password frequently. Here we will go through setting and changing the password for the admin user.

### Retrieve the password
As previously mentioned, the operator's password can be retrieved by running the `get-password` action on the Charmed PostgreSQL application:
```shell
juju run-action postgresql/leader get-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "14"
  results:
    operator-password: eItxBiOYeMf7seSv
  status: completed
  timing:
    completed: 2023-03-20 09:17:51 +0000 UTC
    enqueued: 2023-03-20 09:17:49 +0000 UTC
    started: 2023-03-20 09:17:50 +0000 UTC
```

### Rotate the password
You can change the operator's password to a new random password by entering:
```shell
juju run-action postgresql/leader set-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "16"
  results:
    operator-password: npGdNGNGVtu7SO50
  status: completed
  timing:
    completed: 2023-03-20 09:18:11 +0000 UTC
    enqueued: 2023-03-20 09:18:08 +0000 UTC
    started: 2023-03-20 09:18:10 +0000 UTC
```
Please notice the `status: completed` above which means the password has been successfully updated. The password should be different from the previous password.

### Set the new password
You can change the password to a specific password by entering:
```shell
juju run-action postgresql/leader set-password password=my-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "18"
  results:
    operator-password: my-password
  status: completed
  timing:
    completed: 2023-03-20 09:20:06 +0000 UTC
    enqueued: 2023-03-20 09:20:04 +0000 UTC
    started: 2023-03-20 09:20:05 +0000 UTC
```
The password should match whatever you passed in when you entered the command.
