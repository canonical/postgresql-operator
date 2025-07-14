# Tutorial

This hands-on tutorial aims to help you learn how to deploy Charmed PostgreSQL on machines and become familiar with its available operations.

## Prerequisites

While this tutorial intends to guide you as you deploy Charmed PostgreSQL for the first time, it will be most beneficial if:
- You have some experience using a Linux-based CLI
- You are familiar with PostgreSQL concepts such as the `psql` CLI tool, and [users](https://www.postgresql.org/docs/8.0/user-manag.html).

### Minimum system requirements
- Any Linux operating system that supports snaps
- At least 8GB of RAM
- At least 4 {spellexception}`CPUs`
- At least 50GB of available storage
- Virtualisation support
- `amd64` or `arm64` architecture

---

## Set up the environment

First, we will set up a cloud environment using [Multipass](https://multipass.run/) with [LXD](https://documentation.ubuntu.com/lxd/latest/) and [Juju](https://documentation.ubuntu.com/juju/3.6/). This is the quickest and easiest way to get your machine ready for using Charmed PostgreSQL. 

```{seealso}
To learn about other types of deployment environments and methods, see [](/how-to/deploy/index).
```

### Create a Multipass VM

[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses the [cloud-init](https://cloud-init.io/) standard to install and configure all the necessary parts automatically.

Install Multipass on your machine via the [snap store](https://snapcraft.io/multipass):
```{terminal}
:input: sudo snap install multipass
:user: user
:host: my-pc
```

Spin up a new VM called using [`multipass launch`](https://multipass.run/docs/launch-command). We will call it `my-vm`, and use the [charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml) cloud-init configuration, which will install some necessary software for us.

```{terminal}
:input: multipass launch --cpus 4 --memory 8G --disk 50G --name my-vm charm-dev
:user: user
:host: my-pc
```

This may take several minutes if it's the first time you launch this VM.

As soon as the new VM has started, access it:

```{terminal}
:input: multipass shell my-vm
:user: user
:host: my-pc

Welcome to Ubuntu 24.04.2 LTS (GNU/Linux 6.8.0-63-generic x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/pro
...
```

```{tip}
The files `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log` contain all low-level installation details. 
```

### Set up Juju

Since `my-vm` already has Juju and LXD installed, we can go ahead and [bootstrap](https://documentation.ubuntu.com/juju/3.6/reference/juju-cli/list-of-juju-cli-commands/bootstrap/#details) a cloud. In this tutorial, we will use a local LXD [controller](https://documentation.ubuntu.com/juju/3.6/reference/controller/). 

We will call our new controller “overlord”, but you can give it any name you’d like:

```{terminal}
:input: juju bootstrap localhost overlord
:user: ubuntu
:host: my-vm
``` 

A controller can work with different [models](https://juju.is/docs/juju/model). Set up a specific model for Charmed PostgreSQL named `tutorial`:

```{terminal}
:input: juju add-model tutorial
:user: ubuntu
:host: my-vm
``` 

You can now view the model you created by running the command `juju status`. 

```{terminal}
:input: juju status
:user: ubuntu
:host: my-vm

Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord   localhost/localhost  3.6.8    unsupported  15:31:14+02:00

Model "admin/tutorial" is empty.
```

## Deploy PostgreSQL

To deploy Charmed PostgreSQL, run:

```{terminal}
:input: juju deploy postgresql --channel=16/stable
:user: ubuntu
:host: my-vm
```

Juju will now fetch Charmed PostgreSQL from [Charmhub][Charmhub PostgreSQL VM] and deploy it to the LXD cloud. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

You can track the progress by running:

```{terminal}
:input: juju status --watch 1s
:user: ubuntu
:host: my-vm
```

This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord   localhost/localhost  3.6.8    unsupported  15:38:30+02:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message     
postgresql  16.9     active      1  postgresql  16/stable  843  no                                     

Unit           Workload  Agent  Machine  Public address  Ports     Message    
postgresql/0*  active    idle   0        10.26.224.154   5432/tcp  Primary                                      

Machine  State    Address        Inst id        Base          AZ  Message
0        started  10.26.224.154  juju-1c143d-0  ubuntu@24.04      Running


```

You can also watch juju logs with the [`juju debug-log`](https://juju.is/docs/juju/juju-debug-log) command.

## Access PostgreSQL

In this section, you will learn how to get the credentials of your deployment, connect to the PostgreSQL instance, view its default databases, and finally, create your own new database.

This is where we are introduced to internal database [users](/explanation/users). 

```{caution}
This part of the tutorial accesses PostgreSQL via the charm's `operator` user. This is a superuser with permissions to create roles, databases, and more.

**Do not directly interface with the `operator` user in a production environment.**

In a later section, we will cover how to access PostgreSQL more safely.
```

### Retrieve credentials

The user we will connect to in this tutorial will be `operator`. To retrieve its associated password, create a Juju secret named `tutorial`, specifying a password for our `operator` user. Then, grant it to the charm:

```{terminal}
:input: juju add-secret tutorial operator=mypassword
:user: ubuntu
:host: my-vm

secret:d1ohj30ek0fco390bt9g

:input: juju grant-secret tutorial postgresql
```

```{seealso}
For more information about password management with Juju secrets, see [](/how-to/manage-passwords)
```

One more step is needed for the charm to update the passwords of its internal users based on our new Juju secret: we need to update the charm's `system-users` config option:

```{terminal}
:input: juju config postgresql system-users=secret:d1ohj30ek0fco390bt9g
:user: ubuntu
:host: my-vm
```

```{tip}
Remember to replace the secret URI above with yours! 
```

Now we have all the information required to access PostgreSQL. Run the command below to enter the leader unit's shell:

```{terminal}
:input: juju ssh --container postgresql postgresql/leader bash
:user: ubuntu
:host: my-vm
```

### Create a database

The easiest way to interact with PostgreSQL is via [PostgreSQL interactive terminal `psql`](https://www.postgresql.org/docs/14/app-psql.html), which is already installed on the host you're connected to.

We'll need the IP address associated with the specific application unit we want to interact with. You can find it with `juju status`. 

Since we will use the leader unit to connect to PostgreSQL, we are interested in the address for the unit marked with `*`, like in the output below:

```text
Unit           Workload  Agent  Machine  Public address  Ports     Message    
postgresql/0*  active    idle   0        10.26.224.154   5432/tcp  Primary 
```

While still in the leader unit's shell, run the command below to list all databases currently available. Remember to change the example IP to yours.

```{terminal}
:input: sudo psql --host=10.26.224.154 --username=operator --password --list
:user: ubuntu
:host: juju-1c143d-0

Password:
```

When requested, enter the password that you set earlier when creating the secret. In this example, it would be `mypassword`.

You will now see the list of default databases in the unit. `postgres` is the default database we are connected to, which is used for administrative tasks and creating other databases:

```text
                                                          List of databases
   Name    |  Owner   | Encoding | Locale Provider | Collate |  Ctype  | ICU Locale | ICU Rules |         Access privileges          
-----------+----------+----------+-----------------+---------+---------+------------+-----------+------------------------------------
 postgres  | operator | UTF8     | libc            | C.UTF-8 | C.UTF-8 |            |           | operator=CTc/operator             +
           |          |          |                 |         |         |            |           | backup=c/operator                 +
           |          |          |                 |         |         |            |           | monitoring=CTc/operator           +
           |          |          |                 |         |         |            |           | charmed_databases_owner=c/operator+
           |          |          |                 |         |         |            |           | replication=CTc/operator          +
           |          |          |                 |         |         |            |           | rewind=CTc/operator
 template0 | operator | UTF8     | libc            | C.UTF-8 | C.UTF-8 |            |           | =c/operator                       +
           |          |          |                 |         |         |            |           | operator=CTc/operator
 template1 | operator | UTF8     | libc            | C.UTF-8 | C.UTF-8 |            |           | =c/operator                       +
           |          |          |                 |         |         |            |           | operator=CTc/operator             +
           |          |          |                 |         |         |            |           | backup=c/operator                 +
           |          |          |                 |         |         |            |           | monitoring=c/operator             +
           |          |          |                 |         |         |            |           | charmed_databases_owner=c/operator

```

In order to run queries, we can enter the `psql` interactive terminal by running the following command, again typing the password when requested:

```{terminal}
:input: sudo psql --host=10.1.110.80 --username=operator --password postgres
:user: ubuntu
:host: juju-1c143d-0

Password:
```

After submitting the password, you'll enter an interactive terminal like this:

```text
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

postgres=# 
```

Now you are successfully logged in the `psql` interactive terminal. Here it is possible to execute commands to PostgreSQL directly using PostgreSQL SQL Queries. For example, to show which version of PostgreSQL is installed, run the following command:

```text
postgres=# SELECT version();
                                                               version                                                                
--------------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 16.9 (Ubuntu 16.9-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0, 64-bit
(1 row)
```

We can see that PostgreSQL version 16.9 is installed. From this prompt, to print the list of available databases, we can simply run this command:

```text
postgres=# \l
```

The output should be the same as the one obtained before with `psql`, but this time we did not need to specify any parameters since we are already connected to the PostgreSQL application.

To create and connect to a new sample database, we can run the following commands:

```text
postgres=# CREATE DATABASE mynewdatabase;
postgres=# \c mynewdatabase

You are now connected to database "mynewdatabase" as user "operator".
```

We can now create a new table inside this database:

```text
mynewdatabase=# CREATE TABLE mytable (
	id SERIAL PRIMARY KEY,
	name VARCHAR(50),
	age INT
);
```

and insert an element into it:

```text
mynewdatabase=# INSERT INTO mytable (name, age) VALUES ('Numbat', 20);
```

We can see our new table element by submitting a query:

```text
mynewdatabase=# SELECT * FROM mytable;

 id | name | age
----+------+-----
  1 | Numbat |  20
(1 row)
```

You can try multiple SQL commands inside this environment. Once you're ready, reconnect to the default postgres database and drop the sample database we created:

```text
mynewdatabase=# \c postgres

You are now connected to database "postgres" as user "operator".
postgres=# DROP DATABASE mynewdatabase;
```

When you’re ready to leave the PostgreSQL shell, you can just type `exit`. This will take you back to the host of Charmed PostgreSQL (`postgresql/0`). Exit this host by once again typing exit. Now you will be in your original shell where you first started the tutorial. Here you can interact with Juju and LXD.

## Scale your replicas

The Charmed PostgreSQL operator for machines uses a [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, and automatic failover. The charm uses PostgreSQL’s [synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html) with Patroni to handle replication.

```{seealso}
Learn more about how Juju units work in the context of PostgreSQL replication in [](/explanation/units)
```

```{caution}
This tutorial hosts all replicas on the same machine. 

**This should not be done in a production environment.** 

To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).
```

### Add units

Currently, your deployment has only one Juju unit, known in juju as the **leader unit**. You can think of this as the database's **primary instance**. For each cluster replica, a new Juju unit is created. 

All units are members of the same database cluster.

To add two replicas to your deployed PostgreSQL application, run:

```{terminal}
:input: juju add-unit postgresql -n 2
:user: ubuntu
:host: my-vm
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. 

You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord   localhost/localhost  3.6.8    unsupported  17:04:14+02:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql  16.9     active      3  postgresql  16/stable  843  no

Unit           Workload  Agent  Machine  Public address                          Ports     Message
postgresql/0*  active    idle   0        10.26.224.154                           5432/tcp  Primary
postgresql/1   active    idle   1        10.26.224.142                         
  5432/tcp
postgresql/2   active    idle   2        10.26.224.123                           5432/tcp

Machine  State    Address                                 Inst id        Base          AZ  Message
0        started  10.26.224.154                           juju-1c143d-0  ubuntu@24.04      Running
1        started  10.26.224.142                         
  juju-1c143d-1  ubuntu@24.04      Running
2        started  10.26.224.123                           juju-1c143d-2  ubuntu@24.04      Running
```

### Remove units

Removing a unit from the application scales down the replicas.

Before we scale them down, list all the units with `juju status`. You will see three units: `postgresql/0`, `postgresql/1`, and `postgresql/2`. Each of these units hosts a PostgreSQL replica. 

To remove the replica hosted on the unit `postgresql/2` enter:
```{terminal}
:input: juju remove-unit postgresql/2
:user: ubuntu
:host: my-vm
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord   localhost/localhost  3.6.8    unsupported  17:14:38+02:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql  16.9     active      2  postgresql  16/stable  843  no

Unit           Workload  Agent      Machine  Public address    Ports     Message   
postgresql/0*  active    idle       0        10.26.224.154     5432/tcp                          
postgresql/1   active    executing  1        10.26.224.142     
  5432/tcp   
                                                                                                                                              
Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.26.224.154   juju-1c143d-0  ubuntu@24.04      Running
1        started  10.26.224.142   juju-1c143d-1  ubuntu@24.04      Running
```

## Integrate with other applications

[Integrations](https://documentation.ubuntu.com/juju/3.6/reference/relation/), also known as "relations", are the easiest way to create a user for PostgreSQL in Charmed PostgreSQL. 

Integrations automatically create a username, password, and database for the desired user/application. The best practice is to connect to PostgreSQL via a specific user rather than the admin user, like we did earlier with the `operator` user.

In this tutorial, we will relate to the [data integrator charm](https://charmhub.io/data-integrator). This is a bare-bones charm that allows for central management of database users. It automatically provides credentials and endpoints that are needed to connect with a charmed database application.

To deploy `data-integrator` and associate it to a new database called `test-database`:

```{terminal}
:input: juju deploy data-integrator --config database-name=test-database
:user: ubuntu
:host: my-vm

Deployed "data-integrator" from charm-hub charm "data-integrator", revision 78 in channel latest/stable on ubuntu@22.04/stable
```

Running `juju status` will show you `data-integrator` in a `blocked` state. This is expected, since the relation has not yet been established between `data-integrator` and `postgresql`.

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord   localhost/localhost  3.6.8    unsupported  17:26:58+02:00

App              Version  Status   Scale  Charm            Channel        Rev  Exposed  Message    
data-integrator           blocked      1  data-integrator  latest/stable  78   no       Please relate the data-integrator with the desired product
postgresql       16.9     active       2  postgresql       16/stable      843  no    

Unit                Workload  Agent  Machine  Public address  Ports     Message       
data-integrator/0*  blocked   idle   3        10.26.224.131             Please relate the data-integrator with the desired product
postgresql/0*       active    idle   0        10.26.224.154   5432/tcp  Primary       
postgresql/1        active    idle   1        10.26.224.142   5432/tcp       

Machine  State    Address       Inst id        Base          AZ  Message
0        started  10.26.224.154 juju-1c143d-0  ubuntu@24.04      Running
1        started  10.26.224.142 juju-1c143d-1  ubuntu@24.04      Running
3        started  10.26.224.131 juju-1c143d-3  ubuntu@22.04      Running      
```

Now that the `data-integrator` charm has been set up, we can relate it to PostgreSQL. This will automatically create a username, password, and database for `data-integrator`:

```{terminal}
:input: juju integrate data-integrator postgresql
:user: ubuntu
:host: my-vm
```

Wait for `juju status --watch 1s --relations` to show all applications/units as `active`:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord   localhost/localhost  3.6.8    unsupported  17:29:08+02:00

App              Version  Status  Scale  Charm            Channel        Rev  Exposed  Message     
data-integrator           active      1  data-integrator  latest/stable  78   no                                                                  
postgresql       16.9     active      2  postgresql       16/stable      843  no     

Unit                Workload  Agent  Machine  Public address  Ports     Message       
data-integrator/0*  active    idle   3        10.26.224.131                                                                           
postgresql/0*       active    idle   0        10.26.224.154   5432/tcp  Primary       
postgresql/1        active    idle   1        10.26.224.142   5432/tcp       

Machine  State    Address        Inst id        Base          AZ  Message
0        started  10.26.224.154  juju-1c143d-0  ubuntu@24.04      Running
1        started  10.26.224.142  juju-1c143d-1  ubuntu@24.04      Running
3        started  10.26.224.131  juju-1c143d-3  ubuntu@22.04      Running

Integration provider                   Requirer                               Interface              Type     Message
data-integrator:data-integrator-peers  data-integrator:data-integrator-peers  data-integrator-peers  peer            
postgresql:database                    data-integrator:postgresql             postgresql_client      regular         
postgresql:database-peers              postgresql:database-peers              postgresql_peers       peer            
postgresql:refresh-v-three             postgresql:refresh-v-three             refresh                peer   
postgresql:restart                     postgresql:restart                     rolling_op             peer   
```

<!--TODO with secrets 
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
-->

### Remove the user

Removing the integration automatically removes the user that was created when the integration was created. Enter the following to remove the integration:

```{terminal}
:input: juju remove-relation postgresql data-integrator
:user: ubuntu
:host: my-vm
```
<!-- TODO with secrets (different username?)>
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

```{terminal}
:input: juju integrate data-integrator postgresql
:user: ubuntu
:host: my-vm
```

Re-integrating generates a new user and password:

```text
juju run data-integrator/leader get-credentials
```

You can then connect to the database with these new credentials.

From here you will see all of your data is still present in the database.
-->

## Enable encryption with TLS

[Transport Layer Security (TLS)](https://en.wikipedia.org/wiki/Transport_Layer_Security) is a protocol used to encrypt data exchanged between two applications. Essentially, it secures data transmitted over a network.

Typically, enabling TLS internally within a highly available database or between a highly available database and client/server applications requires a high level of expertise. This has all been encoded into Charmed PostgreSQL so that configuring TLS requires minimal effort on your end.

TLS is enabled by integrating Charmed PostgreSQL with the [Self-signed certificates charm](https://charmhub.io/self-signed-certificates). This charm centralises TLS certificate management consistently and handles operations like providing, requesting, and renewing TLS certificates.

```{caution}
**[Self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) are not recommended for a production environment.**

Check [this guide](https://discourse.charmhub.io/t/security-with-x-509-certificates/11664) for an overview of the TLS certificates charms available. 
```

Before enabling TLS on Charmed PostgreSQL, we must deploy the `self-signed-certificates` charm:

```{terminal}
:input: juju deploy self-signed-certificates --config ca-common-name="Tutorial CA"
:user: ubuntu
:host: my-vm

Deployed "self-signed-certificates" from charm-hub charm "self-signed-certificates", revision 317 in channel 1/stable on ubuntu@24.04/stable
```

Wait until the `self-signed-certificates` app is up and active, use `juju status --watch 1s` to monitor the progress:

```text
Model     Controller  Cloud/Region         Version  SLA          Timestamp                                      
tutorial  overlord   localhost/localhost  3.6.8    unsupported  17:43:44+02:00                                 
                                                                                                                
App                       Version  Status  Scale  Charm                     Channel        Rev  Exposed  Message
data-integrator                    active      1  data-integrator           latest/stable  78   no
postgresql                16.9     active      2  postgresql                16/stable      843  no              
self-signed-certificates           active      1  self-signed-certificates  1/stable       317  no              
                                                                                                                
Unit                         Workload  Agent  Machine  Public address  Ports     Message
data-integrator/0*           active    idle   3        10.26.224.131                    
postgresql/0*                active    idle   0        10.26.224.154   5432/tcp  Primary
postgresql/1                 active    idle   1        10.26.224.142   5432/tcp
self-signed-certificates/0*  active    idle   4        10.26.224.62       
                                                                                                  
Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.26.224.154   juju-1c143d-0  ubuntu@24.04      Running
1        started  10.26.224.142   juju-1c143d-1  ubuntu@24.04      Running
3        started  10.26.224.131   juju-1c143d-3  ubuntu@22.04      Running                   
4        started  10.26.224.62    juju-1c143d-4  ubuntu@24.04      Running       
```

To enable TLS on Charmed PostgreSQL, integrate the two applications:
<!--TODO: check that client-certificates is the correct endpoint -->
```{terminal}
:input: juju integrate postgresql:client-certificates self-signed-certificates:certificates
:user: ubuntu
:host: my-vm
```

Observe the `juju status --watch 1s` as the applications change status for a few seconds. Once they've stabilised back into `active` and `idle` states, the relation has finished forming. PostgreSQL is now using TLS certificate generated by the `self-signed-certificates` charm.

Use `openssl` to connect to the PostgreSQL leader unit and check the TLS certificate in use. Remember to change the IP address and port to yours. 

```{terminal}
:input: openssl s_client -starttls postgres -connect 10.26.224.154:5432
:user: ubuntu
:host: my-vm

CONNECTED(00000003)
Can't use SSL_get_servername
depth=1 CN = Tutorial CA
verify error:num=19:self-signed certificate in certificate chain
...
Certificate chain
 0 s:CN = 10.26.224.154, x500UniqueIdentifier = 641535a5-b196-4e56-b54d-93fc42270667
   i:CN = Tutorial CA
   a:PKEY: rsaEncryption, 2048 (bit); sigalg: RSA-SHA256
   v:NotBefore: Jul 11 15:50:00 2025 GMT; NotAfter: Oct  9 15:50:00 2025 GMT
...
```

Congratulations! PostgreSQL is now using TLS certificate generated by the external application `self-signed-certificates`.

To remove the external TLS, remove the integration:

```{terminal}
:input: juju remove-relation postgresql:client-certificates self-signed-certificates:certificates
:user: ubuntu
:host: my-vm
```

If you once again check the TLS certificates in use via the OpenSSL client, you will see something similar to the output below:

```{terminal}
:input: openssl s_client -starttls postgres -connect 10.26.224.154:5432
:user: ubuntu
:host: my-vm

CONNECTED(00000003)
---
no peer certificate available
---
No client certificate CA names sent
---
...
```

The Charmed PostgreSQL application is not using TLS anymore.

## Clean up your environment

In this tutorial we've successfully deployed PostgreSQL on LXD, accessed a database, scaled our cluster, added and removed database users, and enabled a layer of security with TLS.

You may now keep your Charmed PostgreSQL deployment running and write to databases, or remove it entirely. 

If you'd like to keep your environment for later, simply stop your VM with
```text
multipass stop my-vm
```

If you're done with testing and would like to free up resources on your machine, you can remove the VM entirely.

```{warning}
When you remove VM as shown below, you will lose all the data in PostgreSQL and any other applications inside Multipass VM! 

For more information, see the docs for [`multipass delete`](https://multipass.run/docs/delete-command).
```

**Delete your VM and all its data** by running:

```text
multipass delete --purge my-vm
```

### Next steps

- Run [Charmed PostgreSQL on Kubernetes](https://github.com/canonical/postgresql-k8s-operator)
- Check out our other database charms, like [MySQL](https://charmhub.io/mysql) and [Kafka](https://charmhub.io/kafka?channel=edge)
- Read about [high availability best practices](https://canonical.com/blog/database-high-availability)
- [Report](https://github.com/canonical/postgresql-operator/issues) any problems you encountered
- [Give us your feedback](/reference/contacts)
- [Contribute to the code base](https://github.com/canonical/postgresql-operator)

<!--Links-->

[Charmhub PostgreSQL VM]: https://charmhub.io/postgresql?channel=16/stable