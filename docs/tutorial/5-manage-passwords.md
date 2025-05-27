(tutorial-5-manage-passwords)=


> [Charmed PostgreSQL VM Tutorial](/tutorial/index) > 4. Manage passwords

# Manage passwords

When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time, it is a good practice to change the password frequently. 

In this section, we will go through setting and changing the password for the admin user.

```{caution}
This tutorial is written for **Charmed PostgreSQL 14**, which has a different way of managing passwords than 16.

To learn more about managing passwords on **Charmed PostgreSQL 16**, see [How to > Manage passwords](/how-to-guides/manage-passwords).
```

## Summary
- [Retrieve the operator password](#retrieve-password)
- [Rotate the operator password](#rotate-password)
- [Set a new password](#set-new-password)
  - ...for the operator
  - ...for another user

---

## Retrieve the operator password

The operator's password can be retrieved by running the `get-password` action on the Charmed PostgreSQL VM application:
```shell
juju run postgresql/leader get-password
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

## Rotate the operator password

You can change the operator's password to a new random password by entering:
```shell
juju run postgresql/leader set-password
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
The `status: completed` element in the output above indicates that the password has been successfully updated. The new password should be different from the previous password.

## Set a new password

You can set a specific password for any user by running the `set-password` juju action on the leader unit.   

### ...for the operator user
To set a manual password for the operator/admin user, run the following command:
```shell
juju run postgresql/leader set-password password=<password>
```
where `<password>` is your password of choice.

Example output:
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

### ...for another user
To set a manual password for another user, run the following command:
```shell
juju run postgresql/leader set-password username=my-user password=my-password
```
Read more about internal operator users [here](/explanation/users).

**Next step:** [5. Integrate with other applications](/tutorial/6-integrate-with-other-applications)

