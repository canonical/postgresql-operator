# How to deploy on GCE

[Google Compute Engine](https://cloud.google.com/products/compute) is a popular subsidiary of Google that provides on-demand cloud computing platforms on a metered pay-as-you-go basis. Access the GCloud web console at [console.cloud.google.com](https://console.cloud.google.com/compute/instances).


## Install GCloud and Juju tooling

Install Juju via snap:

```text
sudo snap install juju
sudo snap install google-cloud-cli --classic
```

Check the official the [Google Cloud (GCloud) CLI](https://cloud.google.com/sdk/docs/install)  documentation about other installation options.

To check they are all correctly installed, you can run the commands demonstrated below with sample outputs:

```text
~$ juju version
3.5.4-genericlinux-amd64

~$ gcloud --version
Google Cloud SDK 474.0.0
...
```
### Authenticate

Login to GCloud:

```text
gcloud auth login
```

[Create an service IAM account](https://cloud.google.com/iam/docs/service-accounts-create) for Juju to operate GCE:

```text
> gcloud iam service-accounts create juju-gce-account --display-name="Juju GCE service account"
Created service account [juju-gce-account].

> gcloud iam service-accounts list
DISPLAY NAME                   EMAIL                                                               DISABLED
...
Juju GCE service account       juju-gce-account@canonical-data-123456.iam.gserviceaccount.com      False
...

> gcloud iam service-accounts keys create sa-private-key.json  --iam-account=juju-gce-account@canonical-data-123456.iam.gserviceaccount.com
created key [aaaaaaa....aaaaaaa] of type [json] as [sa-private-key.json] for [juju-gce-account@canonical-data-123456.iam.gserviceaccount.com]

> gcloud projects add-iam-policy-binding canonical-data-123456 --role=roles/compute.admin \
--member serviceAccount:juju-gce-account@canonical-data-123456.iam.gserviceaccount.com
```

## Bootstrap Juju controller on GCE

Move the newly exported GCloud JSON file into a snap-accessible folder due to a known Juju [issue](https://bugs.launchpad.net/juju/+bug/2007575).


```text
sudo mv sa-private-key.json /var/snap/juju/common/sa-private-key.json
sudo chmod a+r /var/snap/juju/common/sa-private-key.json
```

Add GCE credentials to Juju:

```text
> juju add-credential google
...
Enter credential name: juju-gce-account
...

Auth Types
  jsonfile
  oauth2

Select auth type [jsonfile]: jsonfile

Enter path to the .json file containing a service account key for your project
Path: /var/snap/juju/common/sa-private-key.json

Credential "juju-gce-account" added locally for cloud "google".
```

Bootstrap Juju controller ([check all supported configuration options](https://juju.is/docs/juju/google-gce)):
```text
juju bootstrap google gce
```
<details><summary>Output example</summary>

```text
> juju bootstrap google gce
Creating Juju controller "gce" on google/us-east1
Looking for packaged Juju agent version 3.5.4 for amd64
Located Juju agent version 3.5.4-ubuntu-amd64 at https://streams.canonical.com/juju/tools/agent/3.5.4/juju-3.5.4-linux-amd64.tgz
Launching controller instance(s) on google/us-east1...
 - juju-33f662-0 (arch=amd64 mem=3.6G cores=4)
Installing Juju agent on bootstrap instance
Waiting for address
Attempting to connect to 35.231.246.157:22
Attempting to connect to 10.142.0.17:22
Connected to 35.231.246.157
Running machine configuration script...
Bootstrap agent now started
Contacting Juju controller at 35.231.246.157 to verify accessibility...

Bootstrap complete, controller "gce" is now available
Controller machines are in the "controller" model

Now you can run
	juju add-model <model-name>
to create a new model to deploy workloads.
```
</details>

You can check the [GCE instance availability](https://console.cloud.google.com/compute/instances) (ensure the right GCloud project chosen!):

![image|690x172](gce-availability.png)

Create a new Juju model:
```text
juju add-model welcome
```
> (Optional) Increase the debug level if you are troubleshooting charms:
> ```text
> juju model-config logging-config='<root>=INFO;unit=DEBUG'
> ```

## Deploy charms

The following command deploys PostgreSQL and [Data-Integrator](https://charmhub.io/data-integrator) (the charm to request a test DB):

```text
juju deploy postgresql --channel 16/stable
juju deploy data-integrator --config database-name=test123
juju relate postgresql data-integrator
```
Check the status:
```text
> juju status --relations
Model    Controller  Cloud/Region     Version  SLA          Timestamp
welcome  gce         google/us-east1  3.5.4    unsupported  23:24:05+02:00

App              Version  Status  Scale  Charm            Channel        Rev  Exposed  Message
data-integrator           active      1  data-integrator  latest/stable   41  no       
postgresql       16.9     active      1  postgresql       16/stable      843  no       

Unit                Workload  Agent  Machine  Public address  Ports     Message
data-integrator/0*  active    idle   1        34.73.238.173             
postgresql/0*       active    idle   0        35.237.64.81    5432/tcp  Primary

Machine  State    Address        Inst id        Base          AZ          Message
0        started  35.237.64.81   juju-e2b96f-0  ubuntu@22.04  us-east1-b  RUNNING
1        started  34.73.238.173  juju-e2b96f-1  ubuntu@22.04  us-east1-d  RUNNING

Integration provider                   Requirer                               Interface              Type     Message
data-integrator:data-integrator-peers  data-integrator:data-integrator-peers  data-integrator-peers  peer     
postgresql:database                    data-integrator:postgresql             postgresql_client      regular  
postgresql:database-peers              postgresql:database-peers              postgresql_peers       peer     
postgresql:restart                     postgresql:restart                     rolling_op             peer     
postgresql:upgrade                     postgresql:upgrade                     upgrade                peer  
```

Once deployed, request the credentials for your newly bootstrapped PostgreSQL database.

For Juju 2.9 use:
```text
juju run-action --wait data-integrator/leader get-credentials
```
and for newer Juju 3+ use:
```text
juju run data-integrator/leader get-credentials
```

The output example:
```text
postgresql:
  data: '{"database": "test123", "external-node-connectivity": "true", "requested-secrets":
    "[\"username\", \"password\", \"tls\", \"tls-ca\", \"uris\"]"}'
  database: test123
  endpoints: 10.142.0.18:5432
  password: SkQ7KpYsyC6PmVVK
  uris: postgresql://relation-4:SkQ7KpYsyC6PmVVK@10.142.0.18:5432/test123
  username: relation-4
  version: "14.12"
```

At this point, you can access your DB inside GCloud using the internal IP address. All further Juju applications will use the database through the internal network:
```text
> psql postgresql://relation-4:SkQ7KpYsyC6PmVVK@10.142.0.18:5432/test123
psql (14.12 (Ubuntu 14.12-0ubuntu0.22.04.1))
Type "help" for help.

test123=>
```

See our {ref}`how-to` for further information about setting up and operating newly deployed Charmed PostgreSQL.

## Expose database (optional)

If necessary to access DB from outside of GCloud (warning: opening ports to public is risky) open the GCloud firewall using the simple [juju expose](https://juju.is/docs/juju/juju-expose) functionality: 
```text
juju expose postgresql
```

Once exposed, you can connect your database using the same credentials as above (Important: this time use the GCE Public IP assigned to the PostgreSQL instance):
```text
> juju status postgresql
...
Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        35.237.64.81    5432/tcp  Primary
...

> psql postgresql://relation-4:SkQ7KpYsyC6PmVVK@35.237.64.81:5432/test123
psql (15.6 (Ubuntu 15.6-0ubuntu0.23.10.1), server 14.12 (Ubuntu 14.12-0ubuntu0.22.04.1))
Type "help" for help.

test123=>
```
To close the public access run:
```text
juju unexpose postgresql
```
## Clean up

```{caution}
Always clean GCE resources that are no longer necessary -  they could be costly!
```

To destroy the Juju controller and remove GCE instance (warning: all your data will be permanently removed):
```text
> juju controllers
Controller  Model    User   Access     Cloud/Region     Models  Nodes    HA  Version
gce*        welcome  admin  superuser  google/us-east1       2      1  none  3.5.4  

> juju destroy-controller gce --destroy-all-models --destroy-storage --force
```

Next, check and manually delete all unnecessary GCloud resources, to show the list of all your GCE instances run the following command (make sure the correct region used!): 
```text
gcloud compute instances list
```
<details><summary>Output example</summary>

```text
NAME           ZONE        MACHINE_TYPE   PREEMPTIBLE  INTERNAL_IP  EXTERNAL_IP     STATUS
juju-33f662-0  us-east1-b  n1-highcpu-4                10.142.0.17  35.231.246.157  RUNNING
juju-e2b96f-0  us-east1-b  n2d-highcpu-2               10.142.0.18  35.237.64.81    STOPPING
juju-e2b96f-1  us-east1-d  n2d-highcpu-2               10.142.0.19  34.73.238.173   STOPPING
```
</details>

List your Juju credentials:
```text
> juju credentials
...
Client Credentials:
Cloud        Credentials
google       juju-gce-account
...
```
Remove GCloud credentials from Juju:
```text
> juju remove-credential google juju-gce-account
```

Finally, remove GCloud JSON file user credentials (to avoid forgetting and leaking):
```text
rm -f /var/snap/juju/common/sa-private-key.json
```

```{note}
If you expect having several concurrent connections frequently, it is highly recommended to deploy [PgBouncer](https://charmhub.io/pgbouncer?channel=1/stable) alongside PostgreSQL. For more information, read our explanation about [Connection pooling](/explanation/connection-pooling).
```

