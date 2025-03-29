[note]
**Note**: All commands are written for `juju >= v3.1`

If you're using `juju 2.9`, check the [`juju 3.0` Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

Please read this doc in its entirety before deploying anything. In particular, please decide what Parca backend you would like to use. You can either deploy [Charmed Parca K8s](https://charmhub.io/parca-k8s) or use [Polar Signals Cloud](https://www.polarsignals.com/).

# Enable Profiling

This guide contains the steps to enable profiling with [Parca](https://www.parca.dev/docs/overview/) for your PostgreSQL application.

To summarize:
* [Deploy Charmed PostgreSQL](#heading--deploy)
* [Optional: Deploy the COS-lite bundle and Charmed Parca K8s + Offer interfaces for cross-model integrations](#heading--cos)
* [Deploy Charmed Parca Agent and integrate with Charmed PostgreSQL](#heading--parcaagent)
* [Integrate Charmed Parca Agent with the Parca Backend](#heading--parcabackend)
* [View PostgreSQL machine profiles](#heading--view)

<a href="#heading--deploy"><h2 id="heading--deploy"> Deploy Charmed PostgreSQL </h2></a>

See [How to scale units](https://discourse.charmhub.io/t/charmed-postgresql-how-to-scale-units/9689) for reference of how you can deploy Charmed PostgreSQL in a machine model.

[note]
**Note**: If you are deploying Charmed PostgreSQL in a LXD model, you will need to ensure that LXD's virtualization type is set to `virtual-machine` for the Juju application. This is because LXD does not allow `/sys/kernel/tracing` to be mounted in a system container (even in privileged mode) due to security isolation concerns. 

To ensure that a virtual machine is used instead of a system container, you would need to add constraints, e.g. `juju deploy postgresql --constraints="virt-type=virtual-machine"`. 
[/note]

[note]
**Note:** Please note the base of the Charmed PostgreSQL application.

Nothing needs to be done if the base is `ubuntu@24.04` which already loads the kernel symbol table for debugging by default.

If your base is `ubuntu@22.04`, you will need to ensure that your are using the `generic` flavor (see output of `uname -r` to confirm) of Linux. If you do not have the `generic` flavor, you can enable it on a unit to be profiled as follows:

```
juju ssh postgresql/0 bash
sudo apt-get update && sudo apt-get install linux-image-virtual
sudo apt-get autopurge linux-image-kvm
# only run the following if your application is deployed in an LXD model
# rm /etc/default/grub.d/40-force-partuuid.cfg

# Then open the /etc/default/grub file and replace the line that starts with GRUB_DEFAULT= with:
release=$(linux-version list | grep -e '-generic$' | sort -V | tail -n1)
GRUB_DEFAULT="Advanced options for Ubuntu>Ubuntu, with Linux $release"
# Exit out of the /etc/default/grub file

sudo update-grub
sudo reboot
```
[/note]

<a href="#heading--cos"><h2 id="heading--cos"> Optional: Deploy the COS-lite bundle and Charmed Parca K8s + Offer interfaces for cross-model integrations </h2></a>

Please refer to [Getting started on MicroK8s](https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s) and deploy the 'cos-lite' bundle from the `latest/edge` track in a Kubernetes environment.

Then, please refer to [Deploy Charmed Parca on top of COS-lite](https://discourse.charmhub.io/t/how-to-deploy-charmed-parca-on-top-of-cos-lite/16579) to deploy Charmed Parca K8s in the same model as the 'cos-lite' bundle.

Next, offer interfaces for cross-model integrations from the model where Charmed PostgreSQL is deployed.

```
juju offer <parca_k8s_application_name>:parca-store-endpoint
```

<a href="#heading--parcaagent"><h2 id="heading--parcaagent"> Deploy Charmed Parca Agent and Integrate with Charmed PostgreSQL </h2></a>

Switch to the Charmed PostgreSQL model, then deploy Charmed Parca Agent and relate with Charmed PostgreSQL:

```
juju switch <machine_controller_name>:<postgresql_model_name>

juju deploy parca-agent --channel latest/edge
juju integrate postgresql parca-agent
```

<a href="#heading--parcabackend"><h2 id="heading--parcabackend"> Integrate Charmed Parca Agent with the Parca backend </h2></a>

### Integrating with Parca K8s in a K8s model

If you deployed Charmed Parca K8s in a Kubernetes model, consume the parca offer from a previous section and integrate with them:

```
juju switch <machine_controller_name>:<postgresql_model_name>

juju find-offers <k8s_controller_name>:
```

> :exclamation: Do not miss the ":" in the command above.

Below is a sample output where `k8s` is the K8s controller name and `cos` is the model where `cos-lite` and `parca-k8s` are deployed:

```
Store  URL                            Access  Interfaces
k8s    admin/cos.parca                admin   parca_store:parca-store-endpoint
```

Next, consume this offer so that is reachable from the current model. Then relate Charmed Parca Agent with the consumed offer endpoint:

```
juju consume k8s:admin/cos.parca

juju integrate parca-agent parca 
```

### Integrating with Polar Signals Cloud

Please refer to [How to integrate with Polar Signals Cloud](https://discourse.charmhub.io/t/charmed-parca-docs-how-to-integrate-with-polar-signals-cloud/16559). We recommend configuring `parca-agent` to forward profiles to the cloud instance instead of configuring `parca-k8s` server to forward profiles to the cloud instance. This would entail deploying the `polar-signals-cloud-integrator` in the same model as Charmed PostgreSQL.

<a href="#heading--view"><h2 id="heading--view"> View Profiles </h2></a>

After this is complete, the profiles for the machines where the PostgreSQL units are running will be accessible from the Parca web interface. If you are running Charmed Parca K8s, you can also access the link for Parca's web interface from COS catalogue (`juju run traefik/0 show-proxied-endpoints` in the K8s model where `cos-lite` is deployed).

![Example profile with Parca Web UI690x753](upload://zFOOKY8nokrg2Q4xUVTbD8UGjD3.png)

Furthermore, if you have `cos-lite` deployed, you can use Grafana to explore profiles under the `Explore` section with `parca-k8s` as the data source.

![Example profile with Grafana's Parca plugin|690x383](upload://w3G5STYOxMZHCpIA48gEJHUniLi.jpeg)