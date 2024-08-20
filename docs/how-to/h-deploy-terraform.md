# How to deploy using Terraform

[Terraform](https://www.terraform.io/) is an infrastructure automation tool to provision and manage resources in clouds or data centers. To deploy Charmed PostgreSQL using Terraform and Juju, you can use the [Juju Terraform Provider](https://registry.terraform.io/providers/juju/juju/latest). 

The easiest way is to start from [these examples of terraform modules](https://github.com/canonical/terraform-modules) prepared by Canonical. This page will guide you through a deployment using an example module for PostgreSQL on machines.

For an in-depth introduction to the Juju Terraform Provider, read [this Discourse post](https://discourse.charmhub.io/t/6939).

[note]
**Note**: Storage support was added in [Juju Terraform Provider version 0.13+](https://github.com/juju/terraform-provider-juju/releases/tag/v0.13.0).
[/note]

## Summary
* [Install Terraform tooling](#install-terraform-tooling)
* [Verify the deployment](#verify-the-deployment)
* [Apply the deployment](#apply-the-deployment)
* [Check deployment status](#check-deployment-status)
* [Clean up](#clean-up)
---

## Install Terraform tooling

This guide assumes Juju is installed and you have an LXD controller already bootstrapped. For more information, check the [Set up the environment](/t/9709) tutorial page.

First, install Terraform Provider and example modules:
```shell
sudo snap install terraform --classic
```
Switch to the LXD provider and create a new model:
```shell
juju switch lxd
juju add-model my-model
```
Clone examples and navigate to the PostgreSQL machine module:
```shell
git clone https://github.com/canonical/terraform-modules.git
cd terraform-modules/modules/machine/postgresql
```

Initialise the Juju Terraform Provider:
```shell
terraform init
```

## Verify the deployment

Open the `main.tf` file to see the brief contents of the Terraform module:

```tf
resource "juju_application" "machine_postgresql" {
  name  = "postgresql"
  model = "my-model"

  charm {
    name    = "postgresql"
    channel = "14/stable"
  }

  config = {
    plugin_hstore_enable  = true
    plugin_pg_trgm_enable = true
  }

  units = 1
}
```

Run `terraform plan` to get a preview of the changes that will be made:

```shell
terraform plan -var "juju_model_name=my-model"
```

## Apply the deployment

If everything looks correct, deploy the resources (skip the approval):

```shell
terraform apply -auto-approve -var "juju_model_name=my-model"
```

## Check deployment status

Check the deployment status with 

```shell
juju status --model lxd:my-model --watch 1s
```

Sample output:

```shell
Model         Controller  Cloud/Region         Version  SLA          Timestamp
my-model  lxd         localhost/localhost  3.5.2    unsupported  14:04:26+02:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql  14.11    active      1  postgresql  14/stable  429  no       

Unit           Workload  Agent  Machine  Public address  Ports     Message
postgresql/0*  active    idle   0        10.142.152.90   5432/tcp  Primary

Machine  State    Address        Inst id        Base          AZ  Message
0        started  10.142.152.90  juju-1ea4a4-0  ubuntu@22.04      Running
```

Continue to operate the charm as usual from here or apply further Terraform changes.

## Clean up

To keep the house clean, remove the newly deployed Charmed PostgreSQL by running
```shell
terraform destroy -var "juju_model_name=my-model"
```

Sample output:
```shell
juju_application.machine_postgresql: Refreshing state... [id=my-model:postgresql]

Terraform used the selected providers to generate the following execution plan. Resource actions are indicated with the following symbols:
  - destroy

Terraform will perform the following actions:

  # juju_application.machine_postgresql will be destroyed
  - resource "juju_application" "machine_postgresql" {
      - config      = {
          - "plugin_hstore_enable"  = "true"
          - "plugin_pg_trgm_enable" = "true"
        } -> null
      - constraints = "arch=amd64" -> null
      - id          = "my-model:postgresql" -> null
      - model       = "my-model" -> null
      - name        = "postgresql" -> null
      - placement   = "0" -> null
      - storage     = [
          - {
              - count = 1 -> null
              - label = "pgdata" -> null
              - pool  = "rootfs" -> null
              - size  = "99G" -> null
            },
        ] -> null
      - trust       = true -> null
      - units       = 1 -> null

      - charm {
          - base     = "ubuntu@22.04" -> null
          - channel  = "14/stable" -> null
          - name     = "postgresql" -> null
          - revision = 429 -> null
          - series   = "jammy" -> null
        }
    }

Plan: 0 to add, 0 to change, 1 to destroy.

Changes to Outputs:
  - application_name = "postgresql" -> null

Do you really want to destroy all resources?
  Terraform will destroy all your managed infrastructure, as shown above.
  There is no undo. Only 'yes' will be accepted to confirm.

  Enter a value: yes

juju_application.machine_postgresql: Destroying... [id=my-model:postgresql]
juju_application.machine_postgresql: Destruction complete after 1s

Destroy complete! Resources: 1 destroyed.
```
---
[note]
For more examples of Terraform modules for VM, including PostgreSQL HA and PostgreSQL + PgBouncer, see the other directories in the [`terraform-modules` repository](https://github.com/canonical/terraform-modules/tree/main/modules/machine).
[/note]

Feel free to [contact us](/t/11863) if you have any question and [collaborate with us on GitHub](https://github.com/canonical/terraform-modules)!