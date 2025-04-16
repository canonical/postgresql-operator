> [Charmed PostgreSQL VM Tutorial](/t/9707) >  1. Set up the environment

# Set up the environment

In this step, we will set up a development environment with the required components for deploying Charmed PostgreSQL.

[note]
Before you start, make sure your machine meets the [minimum system requirements](/t/11743).
[/note]

## Summary

- [Set up Multipass](#heading--multipass)
- [Set up Juju](#heading--juju)
---

<a href="#heading--multipass"><h2 id="heading--multipass"> Set up Multipass </h2></a>

[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses the [cloud-init](https://cloud-init.io/) standard to install and configure all the necessary parts automatically.

Install Multipass from the [snap store](https://snapcraft.io/multipass):
```shell
sudo snap install multipass
```

Launch a new VM using the [charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml) cloud-init config:
```shell
multipass launch --cpus 4 --memory 8G --disk 50G --name my-vm charm-dev
```

[note type=""]
**Note**: All 'multipass launch' parameters are [described here](https://multipass.run/docs/launch-command).
[/note]

The Multipass [list of commands](https://multipass.run/docs/multipass-cli-commands) is short and self-explanatory. For example, to show all running VMs, just run the command `multipass list`.

As soon as a new VM has started, access it using
```shell
multipass shell my-vm
```

[note]
**Note**:  If at any point you'd like to leave a Multipass VM, enter `Ctrl+D` or type `exit`.
[/note]

All necessary components have been pre-installed inside VM already, like LXD and Juju. The files `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log` contain all low-level installation details. 

<a href="#heading--juju"><h2 id="heading--juju"> Set up Juju </h2></a>

Let's bootstrap Juju to use the local LXD controller. We will call it “overlord”, but you can give it any name you’d like:
```shell
juju bootstrap localhost overlord
```

A controller can work with different [models](https://juju.is/docs/juju/model). Set up a specific model for Charmed PostgreSQL VM named ‘tutorial’:
```shell
juju add-model tutorial
```

You can now view the model you created above by running the command `juju status`.  You should see something similar to the following example output:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  09:38:32+01:00

Model "admin/tutorial" is empty.
```

**Next step:** [2. Deploy PostgreSQL](/t/9697)