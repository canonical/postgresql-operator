>[Charmed PostgreSQL VM Tutorial](https://discourse.charmhub.io/t/9707) > 5. Integrate with other applications

# Integrate with other applications

[Integrations](https://juju.is/docs/sdk/integration), known as "relations" in Juju 2.9, are the easiest way to create a user for PostgreSQL in Charmed PostgreSQL VM. 

Integrations automatically create a username, password, and database for the desired user/application. As mentioned earlier in [2. Deploy PostgreSQL](/t/9697) , it is a better practice to connect to PostgreSQL via a specific user rather than the admin user.

In this section, you will integrate your Charmed PostgreSQL to another charmed application.

## Summary
- [Deploy `data-integrator`](#heading--deploy-data-integrator)
- [Integrate with PostgreSQL](#heading--integrate-with-postgresq)
- [Access the related database](#heading--access-related-database)
- [Remove the user](#heading--remove-user)
---

<a href="#heading--deploy-data-integrator"><h2 id="heading--deploy-data-integrator"> Deploy <code>data-integrator</code> </h2></a>

In this tutorial, we will relate to the [Data Integrator charm](https://charmhub.io/data-integrator). This is a bare-bones charm that allows for central management of database users. It automatically provides credentials and endpoints that are needed to connect with a charmed database application.

To deploy `data-integrator`, run

```shell
juju deploy data-integrator --config database-name=test-database
```
Example output:
```
Located charm "data-integrator" in charm-hub, revision 11
Deploying "data-integrator" from charm-hub charm "data-integrator", revision 11 in channel stable on jammy
```

Running `juju status` will show you `data-integrator` in a `blocked` state. This state is expected due to not-yet established relation (integration) between applications.
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7   unsupported  10:22:13+01:00

App              Version  Status   Scale  Charm            Channel    Rev  Exposed  Message
data-integrator           blocked      1  data-integrator  stable      11  no       Please relate the data-integrator with the desired product
postgresql                active       2  postgresql       14/stable  281  no       

Unit                Workload  Agent  Machine  Public address  Ports  Message
data-integrator/0*  blocked   idle   3        10.89.49.179           Please relate the data-integrator with the desired product
postgresql/0*       active    idle   0        10.89.49.129           
postgresql/1        active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
3        started  10.89.49.179  juju-a8a31d-3  jammy       Running
```

<a href="#heading--integrate-with-postgresql"><h2 id="heading--integrate-with-postgresql"> Integrate with PostgreSQL </h2></a>

Now that the `data-integrator` charm has been set up, we can relate it to PostgreSQL. This will automatically create a username, password, and database for `data-integrator`.

Relate the two applications with:
```shell
juju integrate data-integrator postgresql
```
Wait for `juju status --watch 1s` to show all applications/units as `active`:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  10:22:31+01:00

App              Version  Status  Scale  Charm            Channel    Rev  Exposed  Message
data-integrator           active      1  data-integrator  stable      11  no       
postgresql                active      2  postgresql       14/stable  281  no       

Unit                Workload  Agent  Machine  Public address  Ports  Message
data-integrator/0*  active    idle   3        10.89.49.179           
postgresql/0*       active    idle   0        10.89.49.129           Primary
postgresql/1        active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
3        started  10.89.49.179  juju-a8a31d-3  jammy       Running
```

To retrieve the username, password and database name, run the command
```shell
juju run data-integrator/leader get-credentials
```

Example output:
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
Note that your hostnames, usernames, and passwords will likely be different.

<a href="#heading--access-related-database"><h2 id="heading--access-related-database"> Access the related database </h2></a>

Use `endpoints`, `username`, `password` from above to connect newly created database `test-database` on the PostgreSQL server:
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

When you relate two applications, Charmed PostgreSQL automatically sets up a new user and database for you.
Note the database name we specified when we first deployed the `data-integrator` charm: `--config database-name=test-database`.

<a href="#heading--remove-user"><h2 id="heading--remove-user"> Remove the user </h2></a>

Removing the integration automatically removes the user that was created when the integration was created. Enter the following to remove the integration:
```shell
juju remove-relation postgresql data-integrator
```

Now try again to connect to the same PostgreSQL you just used in the previous section:

```shell
> psql --host=10.89.49.129 --username=relation-3 --password --list
```

This will output an error message like the one shown below:
```
psql: error: connection to server at "10.89.49.129", port 5432 failed: FATAL:  password authentication failed for user "relation-3"
```
This is expected, since this user no longer exists after removing the integration.

Data remains on the server at this stage.

To create a user again, integrate the applications again:
```shell
juju integrate data-integrator postgresql
```
Re-integrating generates a new user and password:
```shell
juju run data-integrator/leader get-credentials
```
You can then connect to the database with these new credentials.
From here you will see all of your data is still present in the database.

**Next step:** [6. Enable TLS](/t/9699)