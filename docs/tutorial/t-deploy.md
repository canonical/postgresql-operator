> [Charmed PostgreSQL VM Tutorial](/t/9707) > 2. Deploy PostgreSQL

# Deploy Charmed PostgreSQL VM

In this section, you will deploy Charmed PostgreSQL VM, access a unit, and interact with the PostgreSQL databases that exist inside the application.

## Summary
- [Deploy PostgreSQL](#heading--deploy)
- [Access PostgreSQL](#heading--access)
  - [Retrieve credentials](#heading--retrieve-credentials)
  - [Access PostgreSQL via `psql`](#heading--psql)
---

<a href="#heading--deploy"><h2 id="heading--deploy"> Deploy PostgreSQL </h2></a>

To deploy Charmed PostgreSQL, all you need to do is run 
```shell
juju deploy postgresql
```

Juju will now fetch Charmed PostgreSQL VM from [Charmhub](https://charmhub.io/postgresql?channel=14/stable) and deploy it to the LXD cloud. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

You can track the progress by running:
```shell
juju status --watch 1s
```

This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  09:41:53+01:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql           active      1  postgresql  14/stable  281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
```

You can also watch juju logs with the [`juju debug-log`](https://juju.is/docs/juju/juju-debug-log) command.
More info on logging in the [juju logs documentation](https://juju.is/docs/olm/juju-logs).

<a href="#heading--access"><h2 id="heading--access"> Access PostgreSQL </h2></a>

[note type="caution"]
 **Warning**: This part of the tutorial accesses PostgreSQL via the `operator` user. 

**Do not directly interface with the `operator` user in a production environment.**

In a later section about [Integrations,](https://charmhub.io/postgresql-k8s/docs/t-integrations) we will cover how to safely access PostgreSQL by creating a separate user via the [Data Integrator charm](https://charmhub.io/data-integrator)
[/note]

<a href="#heading--retrieve-credentials"><h3 id="heading--retrieve-credentials"> Retrieve credentials </h3></a>

Connecting to the database requires that you know the values for `host`, `username` and `password`. 

To retrieve these values, run the Charmed PostgreSQL action `get-password`:
```shell
juju run postgresql/leader get-password
```
Running the command above should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "2"
  results:
    operator-password: <password>
  status: completed
  timing:
    completed: 2023-03-20 08:42:22 +0000 UTC
    enqueued: 2023-03-20 08:42:19 +0000 UTC
    started: 2023-03-20 08:42:21 +0000 UTC
```

To request a password for a different user, use the option `username`:
```shell
juju run postgresql/leader get-password username=replication
```

The IP address of the unit hosting the PostgreSQL application, also referred to as the "host", can be found with `juju status`:
```
...
Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129       
...
```
<a href="#heading--psql"><h3 id="heading--psql"> Access PostgreSQL via <code>psql</code> </h3></a>


To access the units hosting Charmed PostgreSQL, run
```shell
juju ssh postgresql/leader
```

>If at any point you'd like to leave the unit hosting Charmed PostgreSQL K8s, enter `Ctrl+D` or type `exit`.

The easiest way to access PostgreSQL is via the [PostgreSQL interactive terminal `psql`](https://www.postgresql.org/docs/14/app-psql.html), which is already installed here. 

To list all available databases, run:
```shell
psql --host=10.89.49.129 --username=operator --password --list
```
When requested, enter the `<password>` for charm user `operator` that you obtained earlier.

Example output:
```
                              List of databases
   Name    |  Owner   | Encoding | Collate |  Ctype  |   Access privileges   
-----------+----------+----------+---------+---------+-----------------------
 postgres  | operator | UTF8     | C.UTF-8 | C.UTF-8 | 
 template0 | operator | UTF8     | C.UTF-8 | C.UTF-8 | =c/operator          +
           |          |          |         |         | operator=CTc/operator
 template1 | operator | UTF8     | C.UTF-8 | C.UTF-8 | =c/operator          +
           |          |          |         |         | operator=CTc/operator
(3 rows)
```

You can now interact with PostgreSQL directly using [PostgreSQL SQL Queries](https://www.postgresql.org/docs/14/queries.html). For example, entering `SELECT version();` should output something like:
```
> psql --host=10.89.49.129 --username=operator --password postgres
Password: 
psql (14.7 (Ubuntu 14.7-0ubuntu0.22.04.1))
Type "help" for help.

postgres=# SELECT version();
                                                               version                                                                
--------------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.7 (Ubuntu 14.7-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.3.0-1ubuntu1~22.04) 11.3.0, 64-bit
(1 row)
```

Feel free to test out any other PostgreSQL queries. 

When you’re ready to leave the PostgreSQL shell, you can just type `exit`. This will take you back to the host of Charmed PostgreSQL K8s (`postgresql/0`). Exit this host by once again typing `exit`. Now you will be in your original shell where you first started the tutorial. Here you can interact with Juju and LXD.

**Next step:** [3. Scale replicas](/t/9705)