# How to deploy on AWS EC2

[Amazon Web Services](https://aws.amazon.com/) is a popular subsidiary of Amazon that provides on-demand cloud computing platforms on a metered pay-as-you-go basis. Access the AWS web console at [console.aws.amazon.com](https://console.aws.amazon.com/).

## Install AWS and Juju tooling

Install Juju via snap:
```text
sudo snap install juju
```

Follow the installation guides for:
* [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) - the Amazon Web Services CLI

To check they are all correctly installed, you can run the commands demonstrated below with sample outputs:

```text
~$ juju version
3.5.4-genericlinux-amd64

~$ aws --version
aws-cli/2.13.25 Python/3.11.5 Linux/6.2.0-33-generic exe/x86_64.ubuntu.23 prompt/off
```

### Authenticate
[Create an IAM account](https://docs.aws.amazon.com/eks/latest/userguide/getting-started-console.html) (or use legacy access keys) to operate AWS EC2:
```text
mkdir -p ~/.aws && cat <<- EOF >  ~/.aws/credentials.yaml
credentials:
  aws:
    NAME_OF_YOUR_CREDENTIAL:
      auth-type: access-key
      access-key: SECRET_ACCESS_KEY_ID
      secret-key: SECRET_ACCESS_KEY_VALUE
EOF
```

<!--- TODO, teach Juju to use `aws configure` format:
```text
~$ aws configure
AWS Access Key ID [None]: SECRET_ACCESS_KEY_ID
AWS Secret Access Key [None]: SECRET_ACCESS_KEY_VALUE
Default region name [None]: eu-west-3
Default output format [None]:
```
Check AWS credentials:
```text
~$ aws sts get-caller-identity
{
    "UserId": "1234567890",
    "Account": "1234567890",
    "Arn": "arn:aws:iam::1234567890:root"
}
```
--->

## Bootstrap Juju controller on AWS EC2

Add AWS credentials to Juju:

```text
juju add-credential aws -f ~/.aws/credentials.yaml
```

Bootstrap Juju controller ([check all supported configuration options](https://juju.is/docs/juju/amazon-ec2)):

```text
juju bootstrap aws
```
<details><summary>Output example</summary>

```text
> juju bootstrap aws
Creating Juju controller "aws-us-east-1" on aws/us-east-1
Looking for packaged Juju agent version 3.5.4 for amd64
Located Juju agent version 3.5.4-ubuntu-amd64 at https://juju-dist-aws.s3.amazonaws.com/agents/agent/3.5.4/juju-3.5.4-linux-amd64.tgz
Launching controller instance(s) on aws/us-east-1...
 - i-0f4615983d113166d (arch=amd64 mem=8G cores=2)           
Installing Juju agent on bootstrap instance
Waiting for address
Attempting to connect to 54.226.221.6:22
Attempting to connect to 172.31.20.34:22
Connected to 54.226.221.6
Running machine configuration script...
Bootstrap agent now started
Contacting Juju controller at 54.226.221.6 to verify accessibility...

Bootstrap complete, controller "aws-us-east-1" is now available
Controller machines are in the "controller" model

Now you can run
	juju add-model <model-name>
to create a new model to deploy workloads.
```
</details>

<!--TODO: Check this link: [AWS EC2 instance availability](https://us-east-1.console.aws.amazon.com/ec2/home?region=us-east-1#Instances:instanceState=running) -->
You can check the AWS EC2 instance availability (ensure the right AWS region chosen!):
![image|690x118](aws-ec2-availability.png)

Create a new Juju model:

```text
juju add-model welcome
```

Optionally increase the debug level if you are troubleshooting charms:

```text
juju model-config logging-config='<root>=INFO;unit=DEBUG'
```

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
Model    Controller     Cloud/Region   Version  SLA          Timestamp
welcome  aws-us-east-1  aws/us-east-1  3.5.4    unsupported  13:33:05+02:00

App              Version  Status  Scale  Charm            Channel        Rev  Exposed  Message
data-integrator           active      1  data-integrator  latest/stable   41  no       
postgresql       16.9     active      1  postgresql       16/stable      843  no       

Unit                Workload  Agent  Machine  Public address  Ports     Message
data-integrator/0*  active    idle   1        50.19.16.57               
postgresql/0*       active    idle   0        54.224.41.249   5432/tcp  Primary

Machine  State    Address        Inst id              Base          AZ          Message
0        started  54.224.41.249  i-0f374435695ffc54c  ubuntu@22.04  us-east-1b  running
1        started  50.19.16.57    i-061e0d10d36c8cffe  ubuntu@22.04  us-east-1a  running

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
```yaml
postgresql:
  data: '{"database": "test123", "external-node-connectivity": "true", "requested-secrets":
    "[\"username\", \"password\", \"tls\", \"tls-ca\", \"uris\"]"}'
  database: test123
  endpoints: 172.31.40.178:5432
  password: rmnk0AO4jICAixCL
  uris: postgresql://relation-4:rmnk0AO4jICAixCL@172.31.40.178:5432/test123
  username: relation-4
  version: "14.12"
```

At this point, you can access your DB inside AWS using the internal IP address. All further Juju applications will use the database through the internal network:
```text
> psql postgresql://relation-4:rmnk0AO4jICAixCL@172.31.40.178:5432/test123
psql (15.6 (Ubuntu 15.6-0ubuntu0.23.10.1), server 14.12 (Ubuntu 14.12-0ubuntu0.22.04.1))
Type "help" for help.

test123=> 
```

From here you can [use/scale/backup/restore/refresh](/tutorial/index) your newly deployed Charmed PostgreSQL.

## Expose database (optional)

If necessary to access DB from outside of AWS (warning: opening ports to public is risky) open the AWS firewall using the simple [juju expose](https://juju.is/docs/juju/juju-expose) functionality: 
```text
juju expose postgresql
```

Once exposed, you can connect your database using the same credentials as above (Important: this time use the EC2 Public IP assigned to the PostgreSQL instance):
```text
> juju status postgresql
...
Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        54.224.41.249   5432/tcp  Primary
...

> psql postgresql://relation-4:rmnk0AO4jICAixCL@54.224.41.249:5432/test123
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
Always clean AWS resources that are no longer necessary -  they could be costly!
```

To destroy the Juju controller and remove AWS instance (warning: all your data will be permanently removed):
```text
> juju controllers
Controller      Model  User   Access     Cloud/Region   Models  Nodes    HA  Version
aws-us-east-1*  -      admin  superuser  aws/us-east-1       1      1  none  3.5.4  

> juju destroy-controller aws-us-east-1 --destroy-all-models --destroy-storage --force
```

Next, check and manually delete all unnecessary AWS EC2 instances, to show the list of all your EC2 instances run the following command (make sure the correct region used!): 
```text
aws ec2 describe-instances --region us-east-1 --query "Reservations[].Instances[*].{InstanceType: InstanceType, InstanceId: InstanceId, State: State.Name}" --output table
```
<details><summary>Output example</summary>

```text
-------------------------------------------------------
|                  DescribeInstances                  |
+---------------------+----------------+--------------+
|     InstanceId      | InstanceType   |    State     |
+---------------------+----------------+--------------+
|  i-0f374435695ffc54c|  m7i.large     |  terminated  |
|  i-0e1e8279f6b2a08e0|  m7i.large     |  terminated  |
|  i-061e0d10d36c8cffe|  m7i.large     |  terminated  |
|  i-0f4615983d113166d|  m7i.large     |  terminated  |
+---------------------+----------------+--------------+
```
</details>

List your Juju credentials:
```text
> juju credentials
...
Client Credentials:
Cloud        Credentials
aws          NAME_OF_YOUR_CREDENTIAL
...
```
Remove AWS EC2 CLI credentials from Juju:
```text
> juju remove-credential aws NAME_OF_YOUR_CREDENTIAL
```

Finally, remove AWS CLI user credentials (to avoid forgetting and leaking):
```text
rm -f ~/.aws/credentials.yaml
```

```{note}
If you expect having several concurrent connections frequently, it is highly recommended to deploy [PgBouncer](https://charmhub.io/pgbouncer?channel=1/stable) alongside PostgreSQL. For more information, read our explanation about [Connection pooling](/explanation/connection-pooling).
```

