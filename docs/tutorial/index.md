# Tutorial

This hands-on tutorial aims to help you learn how to deploy Charmed PostgreSQL on machines and become familiar with its available operations.

## Prerequisites

While this tutorial intends to guide you as you deploy Charmed PostgreSQL for the first time, it will be most beneficial if:
- You have some experience using a Linux-based CLI
- You are familiar with PostgreSQL concepts such as replication and users.
- Your computer fulfils the [minimum system requirements](/reference/system-requirements)

---

## Set up the environment

First, we will set up a cloud environment using [Multipass](https://multipass.run/) with [LXD](https://documentation.ubuntu.com/lxd/latest/) and [Juju](https://documentation.ubuntu.com/juju/3.6/). This is the quickest and easiest way to get your machine ready for using Charmed PostgreSQL. 

To learn about other types of deployment environments and methods (e.g. bootstrapping other clouds, using Terraform), see [](/how-to/deploy/index).

### Multipass

[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses the [cloud-init](https://cloud-init.io/) standard to install and configure all the necessary parts automatically.

Install Multipass from the [snap store](https://snapcraft.io/multipass):
```text
sudo snap install multipass
```

Spin up a new VM using [`multipass launch`](https://multipass.run/docs/launch-command) with the [charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml) cloud-init configuration:

```text
multipass launch --cpus 4 --memory 8G --disk 50G --name my-vm charm-dev
```

As soon as a new VM has started, access it:

```text
multipass shell my-vm
```

```{tip}
If at any point you'd like to leave a Multipass VM, enter `Ctrl+D` or type `exit`.
```

All necessary components have been pre-installed inside VM already, like LXD and Juju. The files `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log` contain all low-level installation details. 

### Juju

Let's bootstrap Juju to use the local LXD controller. We will call it “overlord”, but you can give it any name you’d like:

```text
juju bootstrap localhost overlord
```

A controller can work with different [models](https://juju.is/docs/juju/model). Set up a specific model for Charmed PostgreSQL VM named ‘tutorial’:
```text
juju add-model tutorial
```

You can now view the model you created above by running the command `juju status`.  You should see something similar to the following example output:
```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  09:38:32+01:00

Model "admin/tutorial" is empty.
```

## Deploy PostgreSQL

To deploy Charmed PostgreSQL, run:

```text
juju deploy postgresql --channel=16/stable
```

Juju will now fetch Charmed PostgreSQL from [Charmhub][Charmhub PostgreSQL VM] and deploy it to the LXD cloud. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

You can track the progress by running:

```text
juju status --watch 1s
```

This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:

```text
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

## Access PostgreSQL

In this section, you will learn how to get the credentials of your deployment, connect to the PostgreSQL instance, view its default databases, and finally, create your own new database.

```{caution}
This part of the tutorial accesses PostgreSQL via the `operator` user. 

**Do not directly interface with the `operator` user in a production environment.**

In a later section about [integrations](#integrate-with-other-applications), we will cover how to safely access PostgreSQL by creating a separate user.
```

### Retrieve credentials

Connecting to the database requires that you know two pieces of information: 
1. The internal PostgreSQL database user credentials (username and password)
2. The host machine's IP address. 

Check the IP addresses associated with each application unit with the `juju status` command. 

Since we will use the leader unit to connect to PostgreSQL, we are interested in the IP address for the unit marked with `*`, like in the output below:

```text
Unit           	  Workload  Agent  Address   Ports  Message
postgresql/0*     active	idle   10.1.110.80     	Primary
```

The user we will connect to in this tutorial will be 'operator'. To retrieve its associated password, run the juju action `get-password`:

```text
juju run postgresql/leader get-password
```
The command above should output something like this:
```text
Running operation 1 with 1 task
  - task 2 on unit-postgresql-0

Waiting for task 2...
password: 66hDfCMm3ofT0yrG
```
In order to retrieve the password of a user other than `operator`, use the `username` option:

```text
juju run postgresql/leader get-password username=replication
```

At this point, we have all the information required to access PostgreSQL. Run the command below to enter the leader unit's shell as root:

```text
juju ssh --container postgresql postgresql/leader bash
```

which should bring you to a prompt like this: 

```text
 root@postgresql-0:/#
```

```{tip}
If you’d like to leave the unit's shell and return to your local terminal, enter `Ctrl+D` or type `exit`.
```

### Create a database

The easiest way to interact with PostgreSQL is via [PostgreSQL interactive terminal `psql`](https://www.postgresql.org/docs/14/app-psql.html), which is already installed on the host you're connected to.

While still in the leader unit's shell, run the command below to list all databases currently available:

```text
psql --host=10.1.110.80 --username=operator --password --list
```

When requested, enter the password that you obtained earlier.

You can see below the output for the list of databases. `postgres` is the default database we are connected to and is used for administrative tasks and for creating other databases:

```text
   Name    |  Owner   | Encoding |   Collate   |    Ctype    |   Access privileges
-----------+----------+----------+-------------+-------------+-----------------------
 postgres  | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 |
 template0 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
 template1 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
(3 rows)
```

In order to execute queries, we should enter psql's interactive terminal by running the following command, again typing password when requested:

```text
 psql --host=10.1.110.80 --username=operator --password postgres
```

The output should be something like this:

```text
psql (14.10 (Ubuntu 14.10-0ubuntu0.22.04.1))
Type "help" for help.

postgres=## 
```

Now you are successfully logged in the interactive terminal. Here it is possible to execute commands to PostgreSQL directly using PostgreSQL SQL Queries. For example, to show which version of PostgreSQL is installed, run the following command:

```text
postgres=## SELECT version();
                                                             version
---------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.10 (Ubuntu 14.10-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0, 64-bit
(1 row)
```

We can see that PostgreSQL version 14.10 is installed. From this prompt, to print the list of available databases, we can simply run this command:

```text
postgres=## \l
```

The output should be the same as the one obtained before with `psql`, but this time we did not need to specify any parameters since we are already connected to the PostgreSQL application.

To create and connect to a new sample database, we can run the following commands:

```text
postgres=## CREATE DATABASE mynewdatabase;
postgres=## \c mynewdatabase

You are now connected to database "mynewdatabase" as user "operator".
```

We can now create a new table inside this database:

```text
postgres=## CREATE TABLE mytable (
	id SERIAL PRIMARY KEY,
	name VARCHAR(50),
	age INT
);
```

and insert an element into it:

```text
postgres=## INSERT INTO mytable (name, age) VALUES ('John', 30);
```

We can see our new table element by submitting a query:

```text
postgres=## SELECT * FROM mytable;

 id | name | age
----+------+-----
  1 | John |  30
(1 row)
```

You can try multiple SQL commands inside this environment. Once you're ready, reconnect to the default postgres database and drop the sample database we created:

```text
postgres=## \c postgres

You are now connected to database "postgres" as user "operator".
postgres=## DROP DATABASE mynewdatabase;
```

When you’re ready to leave the PostgreSQL shell, you can just type `exit`. This will take you back to the host of Charmed PostgreSQL (`postgresql/0`). Exit this host by once again typing exit. Now you will be in your original shell where you first started the tutorial. Here you can interact with Juju and LXD.

## Scale your replicas

The Charmed PostgreSQL VM operator uses a [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, and automatic failover. The charm uses PostgreSQL’s [synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-k8s-synchronous-replication) with Patroni to handle replication.

```{caution}
This tutorial hosts all replicas on the same machine. 

**This should not be done in a production environment.** 

To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).
```

### Add units

Currently, your deployment has only one juju **unit**, known in juju as the **leader unit**. You can think of this as the database **primary instance**. For each **replica**, a new unit is created. All units are members of the same database cluster.

To add two replicas to your deployed PostgreSQL application, run
```text
juju add-unit postgresql -n 2
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. 

You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  10:16:44+01:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql           active      3  postgresql  14/stable  281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           Primary
postgresql/1   active    idle   1        10.89.49.197           
postgresql/2   active    idle   2        10.89.49.175           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
2        started  10.89.49.175  juju-a8a31d-2  jammy       Running
```

### Remove units

Removing a unit from the application scales down the replicas.

Before we scale them down, list all the units with `juju status`. You will see three units: `postgresql/0`, `postgresql/1`, and `postgresql/2`. Each of these units hosts a PostgreSQL replica. 

To remove the replica hosted on the unit `postgresql/2` enter:
```text
juju remove-unit postgresql/2
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  10:17:14+01:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql           active      2  postgresql  14/stable  281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           
postgresql/1   active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
```

## Manage passwords

When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time, it is a good practice to change the password frequently. 

The operator's password can be retrieved by running the `get-password` action on the Charmed PostgreSQL VM application:

```text
juju run postgresql/leader get-password
```
Running the command should output something like:

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

You can change the operator's password to a new random password by entering:

```text
juju run postgresql/leader set-password
```

Running the command should output something like:

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

Learn more about managing user credentials in [](/how-to/manage-passwords) and [](/explanation/users).

## Integrate with other applications

[Integrations](https://juju.is/docs/sdk/integration), known as "relations" in Juju 2.9, are the easiest way to create a user for PostgreSQL in Charmed PostgreSQL VM. 

Integrations automatically create a username, password, and database for the desired user/application. The best practice is to connect to PostgreSQL via a specific user rather than the admin user.

In this tutorial, we will relate to the [data integrator charm](https://charmhub.io/data-integrator). This is a bare-bones charm that allows for central management of database users. It automatically provides credentials and endpoints that are needed to connect with a charmed database application.

To deploy `data-integrator`, run

```text
juju deploy data-integrator --config database-name=test-database
```

Example output:

```text
Located charm "data-integrator" in charm-hub, revision 11
Deploying "data-integrator" from charm-hub charm "data-integrator", revision 11 in channel stable on jammy
```

Running `juju status` will show you `data-integrator` in a `blocked` state. This state is expected due to not-yet established relation (integration) between applications.

```text
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

Now that the `data-integrator` charm has been set up, we can relate it to PostgreSQL. This will automatically create a username, password, and database for `data-integrator`.

Relate the two applications with:

```text
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

```text
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

### Access the related database

Use `endpoints`, `username`, `password` from above to connect newly created database `test-database` on the PostgreSQL server:

```text
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

```text
> psql --host=10.89.49.197 --username=relation-3 --password --list
...
 test-database | operator | UTF8     | C.UTF-8 | C.UTF-8 | =Tc/operator             +
               |          |          |         |         | operator=CTc/operator    +
               |          |          |         |         | "relation-3"=CTc/operator
...
```

When you relate two applications, Charmed PostgreSQL automatically sets up a new user and database for you.
Note the database name we specified when we first deployed the `data-integrator` charm: `--config database-name=test-database`.

### Remove the user

Removing the integration automatically removes the user that was created when the integration was created. Enter the following to remove the integration:

```text
juju remove-relation postgresql data-integrator
```

Now try again to connect to the same PostgreSQL you just used in the previous section:

```text
> psql --host=10.89.49.129 --username=relation-3 --password --list
```

This will output an error message like the one shown below:

```text
psql: error: connection to server at "10.89.49.129", port 5432 failed: FATAL:  password authentication failed for user "relation-3"
```

This is expected, since this user no longer exists after removing the integration.

Data remains on the server at this stage.

To create a user again, integrate the applications again:

```text
juju integrate data-integrator postgresql
```

Re-integrating generates a new user and password:

```text
juju run data-integrator/leader get-credentials
```

You can then connect to the database with these new credentials.

From here you will see all of your data is still present in the database.

## Enable encryption with TLS

[Transport Layer Security (TLS)](https://en.wikipedia.org/wiki/Transport_Layer_Security) is a protocol used to encrypt data exchanged between two applications. Essentially, it secures data transmitted over a network.

Typically, enabling TLS internally within a highly available database or between a highly available database and client/server applications requires a high level of expertise. This has all been encoded into Charmed PostgreSQL so that configuring TLS requires minimal effort on your end.

TLS is enabled by integrating Charmed PostgreSQL with the [Self-signed certificates charm](https://charmhub.io/self-signed-certificates). This charm centralises TLS certificate management consistently and handles operations like providing, requesting, and renewing TLS certificates.

```{caution}
**[Self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) are not recommended for a production environment.**

Check [this guide](https://discourse.charmhub.io/t/security-with-x-509-certificates/11664) for an overview of the TLS certificates charms available. 
```

Before enabling TLS on Charmed PostgreSQL VM, we must deploy the `self-signed-certificates` charm:

```text
juju deploy self-signed-certificates --config ca-common-name="Tutorial CA"
```

Wait until the `self-signed-certificates` is up and active, use `juju status --watch 1s` to monitor the progress:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  10:31:40+01:00

App                        Version  Status  Scale  Charm                      Channel    Rev  Exposed  Message
postgresql                          active      2  postgresql                 14/stable  281  no       
self-signed-certificates            active      1  self-signed-certificates   stable     72   no       

Unit                          Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*                 active    idle   0        10.89.49.129           Primary
postgresql/1                  active    idle   1        10.89.49.197           
self-signed-certificates/0*   active    idle   3        10.89.49.185           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
4        started  10.89.49.185  juju-a8a31d-3  jammy       Running
```

To enable TLS on Charmed PostgreSQL VM, integrate the two applications:

```text
juju integrate postgresql:certificates self-signed-certificates:certificates
```
PostgreSQL is now using TLS certificate generated by the `self-signed-certificates` charm.

Use `openssl` to connect to the PostgreSQL and check the TLS certificate in use. Note that your leader unit's IP address will likely be different to the one shown below:

```text
> openssl s_client -starttls postgres -connect 10.89.49.129:5432 | grep Issuer
...
depth=1 C = US, CN = Tutorial CA
verify error:num=19:self-signed certificate in certificate chain
...
```

Congratulations! PostgreSQL is now using TLS certificate generated by the external application `self-signed-certificates`.

To remove the external TLS, remove the integration:

```text
juju remove-relation postgresql:certificates self-signed-certificates:certificates
```

If you once again check the TLS certificates in use via the OpenSSL client, you will see something similar to the output below:

```text
> openssl s_client -starttls postgres -connect 10.89.49.129:5432
...
no peer certificate available
---
No client certificate CA names sent
...
```

The Charmed PostgreSQL VM application is not using TLS anymore.

## Clean up your environment

In this tutorial we've successfully deployed PostgreSQL on LXD, added and removed cluster members, added and removed database users, and enabled a layer of security with TLS.

You may now keep your Charmed PostgreSQL VM deployment running and write to the database or remove it entirely using the steps in this page. 

If you'd like to keep your environment for later, simply stop your VM with
```text
multipass stop my-vm
```

If you're done with testing and would like to free up resources on your machine, you can remove the VM entirely.

```{warning}
When you remove VM as shown below, you will lose all the data in PostgreSQL and any other applications inside Multipass VM! 

For more information, see the docs for [`multipass delete`](https://multipass.run/docs/delete-command).
```

**Delete your VM and its data** by running:

```text
multipass delete --purge my-vm
```

### Next steps

- Run [Charmed PostgreSQL on Kubernetes](https://github.com/canonical/postgresql-k8s-operator)
- Check out our other other charm offerings, like [MySQL](https://charmhub.io/mysql) and [Kafka](https://charmhub.io/kafka?channel=edge)
- Read about [High Availability best practices](https://canonical.com/blog/database-high-availability)
- [Report](https://github.com/canonical/postgresql-operator/issues) any problems you encountered
- [Give us your feedback](https://chat.charmhub.io/charmhub/channels/data-platform)
- [Contribute to the code base](https://github.com/canonical/postgresql-operator)

<!--Links-->

[Charmhub PostgreSQL VM]: https://charmhub.io/postgresql?channel=16/stable