# Terraform module for Charmed PostgreSQL

This is a Terraform module facilitating the deployment of Charmed PostgreSQL,
using the [Terraform juju provider](https://github.com/juju/terraform-provider-juju/).
For more information, refer to the provider
[documentation](https://registry.terraform.io/providers/juju/juju/latest/docs)
and [deployment tutorial](https://charmhub.io/postgresql/docs/h-deploy-terraform).

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.6.6 |
| juju provider | ~> 1.0 |

## Usage

Users should ensure that Juju model has been created to deploy into:
```
juju add-model welcome
```

The module takes the model UUID rather than the model name. Export it once and reuse it in the examples below:
```
export JUJU_MODEL_UUID=$(juju show-model welcome --format json | jq -r '.welcome.model-uuid')
```

To deploy Charmed PostgreSQL into the model `welcome`, run:
```
terraform apply -var="juju_model=$JUJU_MODEL_UUID" -auto-approve
```

By default, this Terraform module will deploy PostgreSQL with `1` unit only.
To configure the module to deploy `3` units, run:
```
terraform apply -var="juju_model=$JUJU_MODEL_UUID" -var='units=3' -auto-approve
```

The juju storage directives config example:
```
terraform apply -var="juju_model=$JUJU_MODEL_UUID" -auto-approve \
  -var='storage={data="10G", archive="2G,lxd", logs="3G", temp="tmpfs,2G"}'
```

The juju constraints example:
```
terraform apply -var="juju_model=$JUJU_MODEL_UUID" -auto-approve \
  -var='constraints=arch=amd64 cores=4 mem=4096M virt-type=virtual-machine'
```

Example of deploying to the specific Juju machine:
```
juju add-machine
> created machine 19

terraform apply -var="juju_model=$JUJU_MODEL_UUID" -var='machine=19'
```

Example of deploying multiple units to specific Juju machines (e.g. for HA):
```
juju add-machine
> created machine 19
juju add-machine
> created machine 20
juju add-machine
> created machine 21

terraform apply -var="juju_model=$JUJU_MODEL_UUID" -var='machines=["19","20","21"]' -auto-approve
```
The number of machines in the set determines the number of units deployed.

Note: the module variables `units`, `machine` and `machines` are self-exclusive: `machines` takes precedence over `machine`, and either overrides `units`.

Check [Charmed PostgreSQL Deployment How-to](https://charmhub.io/postgresql/docs/h-deploy-terraform) for more examples.

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| juju_model | Juju model UUID to deploy into | `string` | n/a | yes |
| charm_name | Name of the charm on charmhub.io to deploy | `string` | `postgresql` | no |
| app_name | Name of the deployed application in the Juju model | `string` | `postgresql` | no |
| channel | Charm channel to use when deploying | `string` | `16/stable` | no |
| revision | Revision number to deploy charm | `number` | n/a | no |
| base | Application base | `string` | `ubuntu@24.04` | no |
| machine | Target Juju machine to deploy on | `string` | n/a | no |
| machines | Target Juju machines to deploy on | `set(string)` | n/a | no |
| units | Number of units to deploy | `number` | `1` | no |
| constraints | Juju constraints to apply for this application | `string` | `arch=amd64` | no |
| storage | Storage directive | `map(string)` | `{}` | no |
| config | Application configuration. Details at https://charmhub.io/postgresql/configurations | `map(string)` | n/a | no |
| enable_expose | Whether to expose the application | `bool` | `true` | no |

## Outputs

| Name | Description |
|------|-------------|
| application_name | Application name which make up this product module |
| provides | Endpoints charm provides |
| requires | Endpoints charm requires |

