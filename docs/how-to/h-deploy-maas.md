# How to deploy on MAAS

This guide aims to provide a quick start to deploying Charmed PostgreSQL on MAAS. It summarizes the instructions from the [Build a MAAS and LXD environment with Multipass Tutorial](https://discourse.maas.io/t/5360) to set up and tear down a **playground environment**.

If you want to deploy PostgreSQL on MAAS in a **production environment**, refer to the official [Bootstrap MAAS Tutorial](https://maas.io/docs/tutorial-bootstrapping-maas) followed by the [Charmed PostgreSQL Tutorial](/t/9707).



## Summary
 * [Bootstrap a Multipass VM](#heading--bootstrap-multipass-vm)
 * [Configure MAAS](#heading--configure-maas)
 * [Register MAAS with Juju](#heading--register-maas-juju)
 * [Deploy Charmed PostgreSQL on MAAS](#heading--deploy-postgresql-maas)
 * [Test Charmed PostgreSQL deployment](#heading--test-postgresql)
 * [Clean up the environment](#heading--clean-up)

For further details and explanation about each step, remember you can refer to the [original tutorial](https://discourse.maas.io/t/5360). 

---
 <a href="#heading--bootstrap-multipass-vm"><h2 id="heading--bootstrap-multipass-vm"> Bootstrap a Multipass VM </h2></a>

Install Multipass and launch a VM:
```shell
sudo snap install multipass

wget -qO- https://raw.githubusercontent.com/canonical/maas-multipass/main/maas.yml \
 | multipass launch --name maas -c8 -m12GB -d50GB --cloud-init -
```
> The wget command provides a [cloud-init](https://github.com/canonical/maas-multipass/blob/main/maas.yml) file that will set up the VM's LXD and MAAS environment.

 <a href="#heading--configure-maas"><h2 id="heading--configure-maas"> Configure MAAS </h2></a>

**1.** Find your MAAS IP with
```shell
multipass list
```

**2.** Open `http://<MAAS_IP>:5240/MAAS/` and log in with the default credentials: username=`admin`, password=`admin`.

**3.** Complete the additional MAAS configuration in the welcome screen.


<details>
<summary><b>4.</b> Wait for image downloads to complete on <code>http://<MAAS_IP>:5240/MAAS/r/images</code> </summary>

[![Screenshot from 2024-04-12 12-48-40](upload://kyNPhsHr7GHyFouEpp7sxPytb6g.png)](https://assets.ubuntu.com/v1/901aa34b-image_downloads.png)
</details>
</br>

[note]
Make sure you are downloading 22.04 images as well (20.04 is the current default).
[/note]

The LXD machine will be up and running after the images downloading and sync is completed.
<details>
<summary><b>5.</b> Navigate to  <code>http://<MASS_IP>:5240/MAAS/r/tags</code> and create a tag with <code>tag-name=juju</code>. Assign it to the LXD machine. </summary>

[![Screenshot from 2024-04-12 12-51-30](upload://44dY32yFYSybmvypdEgDtj0lFid.png)](https://assets.ubuntu.com/v1/1c82f803-tags.png)
</details>

> **A note on DHCP**
>
> MAAS uses DHCP to boot and install new machines. You must enable DHCP manually if you see this banner on MAAS pages:
![image|690x46](upload://g458TLPPqGIISCFHKdfUwXRepeZ.png)
>
> **Make sure to enable DHCP service inside the MAAS VM only.**
>
 >Use the internal VM network `fabric-1` on `10.10.10.0/24` and choose a range (e.g. `10.10.10.100-10.10.10.120`). Check the [official MAAS manual](https://maas.io/docs/enabling-dhcp) for more information about enabling DHCP.


**6.** Finally, dump MAAS admin user API key to add as Juju credentials later:
```shell
multipass exec maas -- sudo maas apikey --username admin
```

 <a href="#heading--register-maas-juju"><h2 id="heading--register-maas-juju"> Register MAAS with Juju </h2></a>

**1.** Enter the Multipass shell and install juju:
```shell
multipass shell maas
sudo snap install juju
```
**2.** Add MAAS cloud and credentials into juju. 

These commands are interactive, so the following code block shows the commands followed by a sample output. **Make sure to enter your own information when prompted by juju.**
```shell
juju add-cloud

> Since Juju 2 is being run for the first time, downloading latest cloud information. Fetching latest public cloud list... Your list of public clouds is up to date, see `juju clouds`. Cloud Types
>    maas
>    manual
>    openstack
>    oracle
>    vsphere
> 
> Select cloud type: maas
> Enter a name for your maas cloud: maas-cloud 
> Enter the API endpoint url: http://<MAAS_IP>:5240/MAAS
> Cloud "maas-cloud" 
```
```shell
juju add-credential maas-cloud 

> ...
> Enter credential name: maas-credentials
> 
> Regions
>   default
> Select region [any region, credential is not region specific]: default
> ...
> Using auth-type "oauth1". 
> Enter maas-oauth: $(paste the MAAS Keys copied from the output above or from http://YOUR_MAAS_IP:5240/MAAS/r/account/prefs/api-keys ) 
> Credential "maas-credentials" added locally for cloud "maas-cloud".
```

**3.** Bootstrap Juju. 

Add the flags `--credential` if you registered several MAAS credentials, and `--debug` if you want to see bootstrap details:
```shell
juju bootstrap --constraints tags=juju maas-cloud maas-controller
```

# Deploy Charmed PostgreSQL on MAAS
```shell
juju add-model postgresql maas-cloud
juju deploy postgresql --channel 14/stable
```

Sample `juju status` output:
```shell
Model       Controller       Cloud/Region        Version  SLA          Timestamp
postgresql  maas-controller  maas-cloud/default  3.1.8    unsupported  12:50:26+02:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql  14.10    active      1  postgresql  14/stable  363  no       Primary

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        10.10.10.5      5432/tcp  Primary

Machine  State    Address     Inst id        Base          AZ       Message
0        started  10.10.10.5  wanted-dassie  ubuntu@22.04  default  Deployed
```

# Test your Charmed PostgreSQL deployment

Check the [Testing](/t/11773) reference to test your deployment.

 <a href="#heading--clean-up"><h2 id="heading--clean-up"> Clean up the environment </h2></a>
To stop your VM, run: 
```shell
multipass stop maas
```
If you're done with testing and would like to free up resources on your machine, you can remove the VM entirely.

[note type="caution"]
**Warning**: When you remove the VM as shown below, **you will lose all the data** in PostgreSQL and any other applications inside it! 

For more information, see the docs for [`multipass delete`](https://multipass.run/docs/delete-command).
[/note]

To completely delete your VM and all its data, run:
```shell
multipass delete --purge maas
```

[note]
If you expect having several concurrent connections frequently, it is highly recommended to deploy [PgBouncer](https://charmhub.io/pgbouncer?channel=1/stable) alongside PostgreSQL. For more information, read our explanation about [Connection pooling](/t/15777).
[/note]