# Deploy on Juju storage

Charmed PostgreSQL uses the [Juju storage](https://documentation.ubuntu.com/juju/3.6/reference/storage/) abstraction to utilize data volume provided by different [clouds](https://documentation.ubuntu.com/juju/3.6/reference/cloud/#cloud) while keeping the same UI/UX for end users.

* **Charmed PostgreSQL** [**16**](https://charmhub.io/postgresql?channel=16/candidate) supports multiple storage: `archive`, `data`, `logs` and `temp`.
* **Charmed PostgreSQL** [**14**](https://charmhub.io/postgresql?channel=14/stable) supports single storage: `pgdata`.
* [Legacy PostgreSQL charm](/t/10690) in track "[latest](https://charmhub.io/postgresql?channel=14/stable)" does **not** support the Juju storage abstraction.

## Charmed PostgreSQL 16

Check the [`metadata.yaml`](https://github.com/canonical/postgresql-operator/blob/16/edge/metadata.yaml) for find Juju storage name and tech details:

[details="Charmed PostgreSQL 16 storage list"]
```shell
storage:
  archive:
    type: filesystem
    location: /var/snap/charmed-postgresql/common/data/archive
  data:
    type: filesystem
    location: /var/snap/charmed-postgresql/common/var/lib/postgresql
  logs:
    type: filesystem
    location: /var/snap/charmed-postgresql/common/data/logs
  temp:
    type: filesystem
    location: /var/snap/charmed-postgresql/common/data/temp
```
[/details]

Charmed PostgreSQL 16 supports multiple storage `archive` , `data` , `logs` and `temp` . See the deployment examples below.

## Charmed PostgreSQL 14

Check the [metadata.yaml](https://github.com/canonical/postgresql-operator/blob/main/metadata.yaml) for find Juju storage name and tech details:
[details="Charmed PostgreSQL 14 storage list"]
```shell
storage:
  pgdata:
    type: filesystem
    location: /var/snap/charmed-postgresql/common
```
[/details]

Charmed PostgreSQL 14 supports single storage `pgdata` attaching it on `juju deploy` and mounted inside the Snap common folder `/var/snap/charmed-postgresql/common`.

[details="Example of 'Juju storage'"]
```shell
> juju deploy postgresql

> juju storage
Unit          Storage ID  Type        Size  Status   Message
postgresql/0  pgdata/0    filesystem        pending  

> juju storage
Unit          Storage ID  Type        Pool    Size    Status    Message
postgresql/0  pgdata/0    filesystem  rootfs  97 GiB  attached  

> juju show-storage pgdata/0 
pgdata/0:
  kind: filesystem
  life: alive
  status:
    current: attached
    since: 01 May 2025 18:47:04+02:00
  persistent: false
  attachments:
    units:
      postgresql/0:
        machine: "0"
        location: /var/snap/charmed-postgresql/common
        life: alive

> juju ssh postgresql/0 mount | grep /var/snap/charmed-postgresql/common
/dev/sda1 on /var/snap/charmed-postgresql/common type ext4 (rw,relatime,discard,errors=remount-ro)
```
[/details]

## Deployment examples

### Define the storage size

```shell
> juju deploy postgresql --storage pgdata=10G

> juju storage
Unit          Storage ID  Type        Pool  Size    Status    Message
postgresql/1  pgdata/1    filesystem  lxd   10 GiB  attached  
```

### Define the storage location

Juju supports wide list of different [storage pools](https://bobcares.com/blog/lxd-create-storage-pool/):

```shell
> juju create-storage-pool mystoragepool lxd

> juju storage-pools | grep mystoragepool
mystoragepool  lxd       

> juju deploy postgresql --storage pgdata=5G,mystoragepool

> juju storage
Unit          Storage ID  Type        Pool           Size    Status    Message
postgresql/2  pgdata/2    filesystem  mystoragepool  5 GiB   attached  
```

### Re-deploy detached storage (track `14`)

To re-deploy the application with the old Juju storage, it is necessary to provide all charm/database  credentials to the new charm or app. 

**Charmed PostgreSQL 14 uses the Juju action `set-password` to handle credentials:**
```shell
# Note old passwords
> juju show-secret --reveal database-peers.postgresql.app

# Find old storage id:
> juju storage

# Re-deploy new app one unit only to set old passwords
> juju deploy postgresql --channel 14/stable -n 1

# Once deployed, set old passwords from old app Juju App secrets
juju run postgresql/leader set-password username=operator password=cpUJnNRJ6Qt2Hgli 
juju run postgresql/leader set-password username=patroni password=0N7pKAutKCstPuvx
juju run postgresql/leader set-password username=raft password=8xSZvTLfyHpglGfI
juju run postgresql/leader set-password username=replication password=5xN9gj9uu5Um3PWB 
juju run postgresql/leader set-password username=rewind password=wuEsPmsdpc6L8qhT 
juju run postgresql/leader set-password username=monitoring password=AgOxXzcRD5iohE6C

# Scale down to zero units byt removing all Juju units
> juju remove-unit postgresql/1 --destroy-storage=yes

# Scale up to one unit and attach the old Juju storage
> juju add-unit postgresql --attach-storage pgdata/x

# If no issues noticed, scale to HA (3 units total)
> juju add-unit postgresql -n 2
```

### Re-deploy detached storage (track `16`)

To re-deploy the application with the old Juju storage, it is necessary to provide all charm/database credentials as Juju user secrets. 

**Charmed PostgreSQL 16 uses Juju user secrets to handle credentials:**
```shell
# Note old passwords
> juju show-secret --reveal database-peers.postgresql.app

# Add new Juju User secret with old password values
> juju add-secret mypgpass \
  monitoring-password=oQDLAVMV1AHFZq1L \
  operator-password=IXarOnndC9XKoytS \
  patroni-password=vWzJZIktqi0qGMCx \
  raft-password=2pALATzLJsrpAf5q \
  replication-password=2bcnAQhXLrX3ekVP \
  rewind-password=bBqQOXiC7whSqQbR

# Find old storage id:
> juju storage

# Re-deploy new app re-using old storage and old credentials
> juju deploy postgresql \
  --channel 16/candidate \
  --attach-storage pgdata/5 \
  --config system-users=newsecret54321id

# Grant access to new secrets for re-deployed application
> juju grant-secret mypgpass postgresql
```

[details="Example: Complete old storage re-deployment (track 16)"] 
Prepare the test data to restore later:
```shell
# Add a new model
> juju add-model teststorage

# Deploy the new postgresql to dump storage with credentials
> juju deploy postgresql --channel 16/edge --storage pgdata=5Gcompleted
Deployed "postgresql" from charm-hub charm "postgresql", revision 613 in channel 16/edge on ubuntu@24.04/stable

# Wait for deployment completed:
> juju status
...
Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        10.189.210.99   5432/tcp  Primary

# Reveal the Juju secrets for the simpliest DB access:
> juju show-secret --reveal database-peers.postgresql.app
d09v83poie738j7af4n0:
  revision: 2
  checksum: 9e28910800d4dd94cd655d5d5f49a9bb7e4b7fbf6dde232328e4f6734c7bdf84
  owner: postgresql
  label: database-peers.postgresql.app
  created: 2025-05-01T22:22:07Z
  updated: 2025-05-01T22:22:10Z
  content:
    monitoring-password: 6inFApK8IyJuJ6LG
    operator-password: I8mkza6vIhD2w1Rh
    patroni-password: JAYSlB3mQXi9g4fc
    raft-password: eFWUOCYb1lIOpGuM
    replication-password: SFBx8OL9XxXiWwcC
    rewind-password: 92xT4yTniiG7OEch

# Create a test data
> PGPASSWORD=I8mkza6vIhD2w1Rh psql -h 10.189.210.99 -U operator -d postgres -c "create table a (id int);"                                                                              
CREATE TABLE
> PGPASSWORD=I8mkza6vIhD2w1Rh psql -h 10.189.210.99 -U operator -d postgres -c "\d"
        List of relations
 Schema | Name | Type  |  Owner   
--------+------+-------+----------
 public | a    | table | operator
(1 row)

# Check the storage status
> juju storage
Unit          Storage ID  Type        Pool  Size     Status    Message
postgresql/0  pgdata/0    filesystem  lxd   5.0 GiB  attached  

# Remove the old application keeping the storage:
> juju remove-application postgresql --destroy-storage=false
WARNING This command will perform the following actions:
will remove application postgresql
- will remove unit postgresql/0
- will detach storage pgdata/0
Continue [y/N]? y

# Check the status (app and secrets are gone, but storage stays):
> juju status
Model        Controller  Cloud/Region         Version  SLA          Timestamp
teststorage  lxd         localhost/localhost  3.6.5    unsupported  00:28:44+02:00

Model "admin/teststorage" is empty.

> juju secrets
ID  Name  Owner  Rotation  Revision  Last updated

> juju storage
Unit  Storage ID  Type        Pool  Size     Status    Message
      pgdata/0    filesystem  lxd   5.0 GiB  detached  
```

Re-deploy the postgresql application reusing storage `pgdata/0 `:
```shell
# Create a new Juju User secret
> juju add-secret mypgpass \
    monitoring-password=6inFApK8IyJuJ6LG \
    operator-password=I8mkza6vIhD2w1Rh \
    patroni-password=JAYSlB3mQXi9g4fc \
    raft-password=eFWUOCYb1lIOpGuM \
    replication-password=SFBx8OL9XxXiWwcC \
    rewind-password=92xT4yTniiG7OEch
secret:d09vcn1oie738j7af4ng

# Re-deploy app with old storage and old passwords
> juju deploy postgresql \
  --channel 16/edge \
  --attach-storage pgdata/0 \
  --config system-users=d09vcn1oie738j7af4ng
Deployed "postgresql" from charm-hub charm "postgresql", revision 613 in channel 16/edge on ubuntu@24.04/stable

# Grant new application access to manually created Juju User secret
> juju grant-secret mypgpass postgresql

# Wait for deployment 
> juju status
...
Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/1*  active    idle   1        10.189.210.179  5432/tcp  Primary

# Check the old data access once deployment completed (use new App IP!):
> PGPASSWORD=I8mkza6vIhD2w1Rh psql -h 10.189.210.179 -U operator -d postgres -c "\d"
        List of relations
 Schema | Name | Type  |  Owner   
--------+------+-------+----------
 public | a    | table | operator
(1 row)

# Old storage re-used:
> juju storage
Unit          Storage ID  Type        Pool  Size     Status    Message
postgresql/1  pgdata/0    filesystem  lxd   5.0 GiB  attached  
```
[/details]