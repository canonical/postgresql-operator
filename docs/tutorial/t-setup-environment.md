# Environment Setup

This is part of the [Charmed PostgreSQL Tutorial](TODO). Please refer to this page for more information and the overview of the content.

## Minimum requirements
Before we start, make sure your machine meets the following requirements:
- Ubuntu 20.04 (Focal) or later.
- 8GB of RAM.
- 2 CPU threads.
- At least 20GB of available storage.
- Access to the internet for downloading the required snaps and charms.

## Multipass environment
[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses "[cloud-init](https://cloud-init.io/)" standard to install and configure all the necessary parts automatically.

Let's install Multipass from [Snap](https://snapcraft.io/multipass) and launch a new VM using "[charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml)" cloud-init config:
```shell
sudo snap install multipass && \
multipass launch --cpus 4 --memory 8G --disk 30G --name my-vm charm-dev # tune CPU/RAM/HDD accordingly to your needs 
```
*Note: all 'multipass launch' params are [described here](https://multipass.run/docs/launch-command)*.

Multipass [list of commands](https://multipass.run/docs/multipass-cli-commands) is short and self-explanatory, e.g. show all running VMs:
```shell
multipass list
```

As soon as new VM started, enter inside using:
```shell
multipass shell my-vm
```
*Note: if at any point you'd like to leave Multipass VM, enter `Ctrl+d` or type `exit`*.

All the parts have been pre-installed inside VM already, like LXD and Juju (the files '/var/log/cloud-init.log' and '/var/log/cloud-init-output.log' contain all low-level installation details). Let's bootstrap Juju to use local LXD:
```shell
juju bootstrap localhost overlord
```

The controller can work with different models; models host applications such as Charmed PostgreSQL. Set up a specific model for Charmed PostgreSQL named ‘tutorial’:
```shell
juju add-model tutorial
```

You can now view the model you created above by entering the command `juju status` into the command line. You should see the following:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  09:38:32+01:00

Model "admin/tutorial" is empty.
```
