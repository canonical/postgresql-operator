# How to deploy on LXD

For a detailed walkthrough of deploying the charm on LXD, refer to the [Charmed PostgreSQL Tutorial](/t/9707).

For a short summary of the commands on Ubuntu 22.04 LTS, see below:
```shell
sudo snap install multipass
multipass launch --cpus 4 --memory 8G --disk 30G --name my-vm charm-dev # tune CPU/RAM/HDD accordingly to your needs
multipass shell my-vm

juju add-model postgresql
juju deploy postgresql --channel 14/stable
```

The expected result from `juju status` is something similar to:
```shell
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  overlord    localhost/localhost  2.9.42   unsupported  09:41:53+01:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql           active      1  postgresql  14/stable  281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
```

Check the [Testing](/t/11773) reference to test your deployment.