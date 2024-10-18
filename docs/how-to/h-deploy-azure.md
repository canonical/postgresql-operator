# How to deploy on Azure

[Azure](https://azure.com/) is the cloud computing platform developed by Microsoft. It has management, access and development of applications and services to individuals, companies, and governments through its global infrastructure. Access the Azure web console at [portal.azure.com](https://portal.azure.com/).

## Summary
* [Install Juju and Azure tooling](#install-juju-and-azure-tooling)
  * [Authenticate](#authenticate)
* [Bootstrap Juju controller on Azure](#bootstrap-juju-controller-on-azure)
* [Deploy charms](#deploy-charms)
* [Expose database (optional)](#expose-database-optional)
* [Clean up](#clean-up)

---

## Install Juju and Azure tooling

> **WARNING**: the described here `Azure interactive` method (with WEB browser authentication `service-principal-secret-via-browser`) is only supported starting Juju 3.6-rc1+!

Install Juju via snap:
```shell
sudo snap install juju --channel 3.6/edge
```

Follow the installation guides for:
* [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux?pivots=apt) - the Azure CLI for Linux

To check they are all correctly installed, you can run the commands demonstrated below with sample outputs:

```console
~$ juju version
3.6-rc1-genericlinux-amd64

~$ az --version
azure-cli                         2.65.0
core                              2.65.0
telemetry                          1.1.0

Dependencies:
msal                              1.31.0
azure-mgmt-resource               23.1.1
...

Your CLI is up-to-date.
```

### Authenticate

Please follow [the official Juju Azure documentation](https://juju.is/docs/juju/microsoft-azure) and check [the extra explanation about possible options](/t/15219). Choose the authentication method which fits you best. We are describing here the currently recommended `interactive` method with WEB browser authentication `service-principal-secret-via-browser`. Using this method, it is not necessary to login Azure CLI locally, but requires pre-created Azure Subscription.

The first mandatory step is to [create Azure subscription](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/create-subscription) (you will need the Azure Subscription ID for Juju). Once you have it, add Azure credentials to Juju:

> **IMPORTANT**: consider to enter unique `application-name` and `role-definition-name` fields!

```shell
> juju add-credential azure
This operation can be applied to both a copy on this client and to the one on a controller.
No current controller was detected and there are no registered controllers on this client: either bootstrap one or register one.
Enter credential name: azure-test-credentials1

Regions
  centralus
  eastus
   ...

Select region [any region, credential is not region specific]: eastus

Auth Types
  interactive
  service-principal-secret
  managed-identity

Select auth type [interactive]: interactive

Enter subscription-id: [USE-YOUR-REAL-AZURE-SUBSCRIPTION-ID]

Enter application-name (optional): azure-test-name1

Enter role-definition-name (optional): azure-test-role1

Note: your user account needs to have a role assignment to the
Azure Key Vault application (....).
You can do this from the Azure portal or using the az cli:
  az ad sp create --id ...

Initiating interactive authentication.

To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code HIDDEN to authenticate.
To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code HIDDEN to authenticate.
Credential "azure-test-credentials1" added locally for cloud "azure".
```

Once successfully completed, bootstrap the new Juju controller on Azure:
```shell
> juju bootstrap azure azure
Creating Juju controller "azure" on azure/centralus
Looking for packaged Juju agent version 3.6-rc1 for amd64
No packaged binary found, preparing local Juju agent binary
Launching controller instance(s) on azure/centralus...
 - juju-aeb5ea-0 (arch=amd64 mem=3.5G cores=1)
Installing Juju agent on bootstrap instance
Waiting for address
Attempting to connect to 192.168.16.4:22
Attempting to connect to 172.170.35.99:22
Connected to 172.170.35.99
Running machine configuration script...
Bootstrap agent now started
Contacting Juju controller at 192.168.16.4 to verify accessibility...

Bootstrap complete, controller "azure" is now available
Controller machines are in the "controller" model

Now you can run
	juju add-model <model-name>
to create a new model to deploy workloads.
```

You can check the [Azure instances availability](https://portal.azure.com/#browse/Microsoft.Compute%2FVirtualMachines):

![image|689x313](upload://bB5lCMIHtL1KToftKQVv7z86aoi.png)

Create a new Juju model:
```shell
juju add-model welcome
```
> (Optional) Increase the debug level if you are troubleshooting charms:
> ```shell
> juju model-config logging-config='<root>=INFO;unit=DEBUG'
> ```

## Deploy charms

The following command deploys PostgreSQL and [Data-Integrator](https://charmhub.io/data-integrator) (the charm to request a test DB):

```shell
juju deploy postgresql
juju deploy data-integrator --config database-name=test123
juju relate postgresql data-integrator
```
Check the status:
```shell
> juju status --relations
Model    Controller  Cloud/Region     Version    SLA          Timestamp
welcome  azure       azure/centralus  3.6-rc1.1  unsupported  12:56:16+02:00

App              Version  Status  Scale  Charm            Channel        Rev  Exposed  Message
data-integrator           active      1  data-integrator  latest/stable   41  no       
postgresql       14.12    active      1  postgresql       14/stable      468  no       

Unit                Workload  Agent  Machine  Public address  Ports     Message
data-integrator/0*  active    idle   1        172.170.35.131            
postgresql/0*       active    idle   0        172.170.35.199  5432/tcp  Primary

Machine  State    Address         Inst id        Base          AZ  Message
0        started  172.170.35.199  juju-491ebe-0  ubuntu@22.04      
1        started  172.170.35.131  juju-491ebe-1  ubuntu@22.04      

Integration provider                   Requirer                               Interface              Type     Message
data-integrator:data-integrator-peers  data-integrator:data-integrator-peers  data-integrator-peers  peer     
postgresql:database                    data-integrator:postgresql             postgresql_client      regular  
postgresql:database-peers              postgresql:database-peers              postgresql_peers       peer     
postgresql:restart                     postgresql:restart                     rolling_op             peer     
postgresql:upgrade                     postgresql:upgrade                     upgrade                peer     
```

Once deployed, request the credentials for your newly bootstrapped PostgreSQL database:
```shell
juju run data-integrator/leader get-credentials
```

The output example:
```shell
postgresql:
  data: '{"database": "test123", "external-node-connectivity": "true", "requested-secrets":
    "[\"username\", \"password\", \"tls\", \"tls-ca\", \"uris\"]"}'
  database: test123
  endpoints: 192.168.0.5:5432
  password: Jqi0QckCAADOFagl
  uris: postgresql://relation-4:Jqi0QckCAADOFagl@192.168.0.5:5432/test123
  username: relation-4
  version: "14.12"
```

At this point, you can access your DB inside Azure VM using the internal IP address. All further Juju applications will use the database through the internal network:
```shell
> psql postgresql://relation-4:Jqi0QckCAADOFagl@192.168.0.5:5432/test123
psql (14.12 (Ubuntu 14.12-0ubuntu0.22.04.1))
Type "help" for help.

test123=> 
```

From here you can [use/scale/backup/restore/refresh](/t/9707) your newly deployed Charmed PostgreSQL.

## Expose database (optional)

If necessary to access DB from outside of Azure (warning: [opening ports to public is risky](https://www.beyondtrust.com/blog/entry/what-is-an-open-port-what-are-the-security-implications)) open the Azure firewall using the simple [juju expose](https://juju.is/docs/juju/juju-expose) functionality: 
```shell
juju expose postgresql
```

Once exposed, you can connect your database using the same credentials as above (Important: this time use the Azure VM Public IP assigned to the PostgreSQL instance):
```shell
> juju status postgresql
...
Model    Controller  Cloud/Region     Version    SLA          Timestamp
welcome  azure       azure/centralus  3.6-rc1.1  unsupported  13:11:26+02:00

App              Version  Status  Scale  Charm            Channel        Rev  Exposed  Message
data-integrator           active      1  data-integrator  latest/stable   41  no       
postgresql       14.12    active      1  postgresql       14/stable      468  yes       

Unit                Workload  Agent  Machine  Public address  Ports     Message
data-integrator/0*  active    idle   1        172.170.35.131            
postgresql/0*       active    idle   0        172.170.35.199  5432/tcp  Primary

Machine  State    Address         Inst id        Base          AZ  Message
0        started  172.170.35.199  juju-491ebe-0  ubuntu@22.04      
1        started  172.170.35.131  juju-491ebe-1  ubuntu@22.04      

Integration provider                   Requirer                               Interface              Type     Message
data-integrator:data-integrator-peers  data-integrator:data-integrator-peers  data-integrator-peers  peer     
postgresql:database                    data-integrator:postgresql             postgresql_client      regular  
postgresql:database-peers              postgresql:database-peers              postgresql_peers       peer     
postgresql:restart                     postgresql:restart                     rolling_op             peer     
postgresql:upgrade                     postgresql:upgrade                     upgrade                peer     
...

> psql postgresql://relation-4:Jqi0QckCAADOFagl@172.170.35.199:5432/test123
psql (14.12 (Ubuntu 14.12-0ubuntu0.22.04.1))
Type "help" for help.

test123=> 
```
To close the public access run:
```shell
juju unexpose postgresql
```

## Clean up

[note type="caution"]
Always clean Azure resources that are no longer necessary -  they could be costly!
[/note]

To destroy the Juju controller and remove Azure instance (warning: all your data will be permanently removed):
```shell
> juju controllers
...
Controller  Model    User   Access     Cloud/Region     Models  Nodes    HA  Version
azure*      welcome  admin  superuser  azure/centralus       2      1  none  3.6-rc1.1  

> juju destroy-controller azure --destroy-all-models --destroy-storage --force
```

Next, check and manually delete all unnecessary Azure VM instances, to show the list of all your Azure VMs run the following command (make sure no running resources left): 
```shell
az vm list
```

List your Juju credentials:
```shell
> juju credentials
...
Client Credentials:
Cloud        Credentials
azure        azure-test-name1
...
```
Remove Azure CLI credentials from Juju:
```shell
> juju remove-credential azure azure-test-name1
```

Finally, logout Azure CLI (to avoid forgetting and leaking):
```shell
az logout
```