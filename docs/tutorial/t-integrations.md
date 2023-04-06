# Integrating your Charmed PostgreSQL

This is part of the [Charmed PostgreSQL Tutorial](TODO). Please refer to this page for more information and the overview of the content.

## Integrations (Relations for Juju 2.9)
Relations, or what Juju 3.0+ documentation [describes as an Integration](https://juju.is/docs/sdk/integration), are the easiest way to create a user for PostgreSQL in Charmed PostgreSQL. Relations automatically create a username, password, and database for the desired user/application. As mentioned earlier in the [Access PostgreSQL section](#access-PostgreSQL) it is a better practice to connect to PostgreSQL via a specific user rather than the admin user.

### Data Integrator Charm
Before relating to a charmed application, we must first deploy our charmed application. In this tutorial we will relate to the [Data Integrator Charm](https://charmhub.io/data-integrator). This is a bare-bones charm that allows for central management of database users, providing support for different kinds of data platforms (e.g. PostgreSQL, MySQL, MongoDB, Kafka, etc) with a consistent, opinionated and robust user experience. In order to deploy the Data Integrator Charm we can use the command `juju deploy` we have learned above:

```shell
juju deploy data-integrator --channel edge --config database-name=test-database
```
The expected output:
```
Located charm "data-integrator" in charm-hub, revision 6
Deploying "data-integrator" from charm-hub charm "data-integrator", revision 6 in channel edge on jammy
```

Checking the deployment progress using `juju status` will show you the `blocked` state for newly deployed charm:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:22:13+01:00

App              Version  Status   Scale  Charm            Channel  Rev  Exposed  Message
data-integrator           blocked      1  data-integrator  edge       6  no       Please relate the data-integrator with the desired product
postgresql                active       2  postgresql       edge     281  no       

Unit                Workload  Agent  Machine  Public address  Ports  Message
data-integrator/0*  blocked   idle   3        10.89.49.179           Please relate the data-integrator with the desired product
postgresql/0*       active    idle   0        10.89.49.129           
postgresql/1        active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
3        started  10.89.49.179  juju-a8a31d-3  jammy       Running
```
The `blocked` state is expected due to not-yet established relation (integration) between applications.

### Relate to PostgreSQL
Now that the Database Integrator Charm has been set up, we can relate it to PostgreSQL. This will automatically create a username, password, and database for the Database Integrator Charm. Relate the two applications with:
```shell
juju relate data-integrator postgresql
```
Wait for `juju status --watch 1s` to show all applications/units as `active`:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:22:31+01:00

App              Version  Status  Scale  Charm            Channel  Rev  Exposed  Message
data-integrator           active      1  data-integrator  edge       6  no       
postgresql                active      2  postgresql       edge     281  no       

Unit                Workload  Agent  Machine  Public address  Ports  Message
data-integrator/0*  active    idle   3        10.89.49.179           
postgresql/0*       active    idle   0        10.89.49.129           Primary
postgresql/1        active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
3        started  10.89.49.179  juju-a8a31d-3  jammy       Running
```

To retrieve information such as the username, password, and database. Enter:
```shell
juju run-action data-integrator/leader get-credentials --wait
```
This should output something like:
```yaml
unit-data-integrator-0:
  UnitId: data-integrator/0
  id: "20"
  results:
    ok: "True"
    postgresql:
      database: test-database
      endpoints: 10.89.49.129:5432
      password: 136bvw0s7FjJ6mxZ
      read-only-endpoints: 10.89.49.197:5432
      username: relation-3
      version: "14.7"
  status: completed
  timing:
    completed: 2023-03-20 09:22:50 +0000 UTC
    enqueued: 2023-03-20 09:22:46 +0000 UTC
    started: 2023-03-20 09:22:50 +0000 UTC
```
*Note: your hostnames, usernames, and passwords will likely be different.*

### Access the related database
Use `endpoints`, `username`, `password` from above to connect newly created database `test-database` on PostgreSQL server:
```shell
> psql --host=10.89.49.129 --username=relation-3 --password test-database
...
test-database=> \l
...
 test-database | operator | UTF8     | C.UTF-8 | C.UTF-8 | =Tc/operator             +
               |          |          |         |         | operator=CTc/operator    +
               |          |          |         |         | "relation-3"=CTc/operator
...
```

The newly created database `test-database` is also available on all other PostgreSQL cluster members:
```shell
> psql --host=10.89.49.197 --username=relation-3 --password --list
...
 test-database | operator | UTF8     | C.UTF-8 | C.UTF-8 | =Tc/operator             +
               |          |          |         |         | operator=CTc/operator    +
               |          |          |         |         | "relation-3"=CTc/operator
...
```

When you relate two applications Charmed PostgreSQL automatically sets up a new user and database for you.
Please note the database name we specified when we first deployed the `data-integrator` charm: `--config database-name=test-database`.

### Remove the user
To remove the user, remove the relation. Removing the relation automatically removes the user that was created when the relation was created. Enter the following to remove the relation:
```shell
juju remove-relation postgresql data-integrator
```

Now try again to connect to the same PostgreSQL you just used in [Access the related database](#access-the-related-database):
```shell
> psql --host=10.89.49.129 --username=relation-3 --password --list
```

This will output an error message:
```
psql: error: connection to server at "10.89.49.129", port 5432 failed: FATAL:  password authentication failed for user "relation-3"
```
As this user no longer exists. This is expected as `juju remove-relation postgresql data-integrator` also removes the user.
Note: data stay remain on the server at this stage!

Relate the two applications again if you wanted to recreate the user:
```shell
juju relate data-integrator postgresql
```
Re-relating generates a new user and password:
```shell
juju run-action data-integrator/leader get-credentials --wait
```
You can connect to the database with this new credentials.
From here you will see all of your data is still present in the database.
