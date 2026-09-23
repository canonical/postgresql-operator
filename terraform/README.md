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
| juju provider | ~> 1.0 (>= 1.0.0, < 2.0.0) |

## Usage

Users should ensure that Juju model has been created to deploy into, and take note of its UUID:
```
juju add-model welcome
juju show-model welcome --format=json | jq -r '.welcome."model-uuid"'
```

To deploy Charmed PostgreSQL into the model `welcome`, run:
```
terraform apply -var='model_uuid=<model-uuid>' -auto-approve
```

By default, this Terraform module will deploy PostgreSQL with `1` unit only.
To configure the module to deploy `3` units, run:
```
terraform apply -var='model_uuid=<model-uuid>' -var='units=3' -auto-approve
```

The juju storage directives config example:
```
terraform apply -var='model_uuid=<model-uuid>' -auto-approve \
  -var='storage={data="10G", archive="2G,lxd", logs="3G", temp="tmpfs,2G"}'
```

The juju constraints example:
```
terraform apply -var='model_uuid=<model-uuid>' -auto-approve \
  -var='constraints=arch=amd64 cores=4 mem=4096M virt-type=virtual-machine'
```

Example of deploying to the specific Juju machine:
```
juju add-machine
> created machine 19

terraform apply -var='model_uuid=<model-uuid>' -var='machine=19'
```
Note: the module variables `units` and `machine` are self-exclusive.

Check [Charmed PostgreSQL Deployment How-to](https://charmhub.io/postgresql/docs/h-deploy-terraform) for more examples.

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| model_uuid | Juju model uuid (to deployed into) | `string` | n/a | yes |
| juju_model | Deprecated: UUID of the Juju model. Use `model_uuid` instead | `string` | n/a | no |
| charm_name | Name of the charm on charmhub.io to deploy | `string` | `postgresql` | no |
| app_name | Name of the deployed application in the Juju model | `string` | `postgresql` | no |
| channel | Charm channel to use when deploying | `string` | `16/stable` | no |
| revision | Revision number to deploy charm | `number` | n/a | no |
| base | Application base | `string` | `ubuntu@24.04` | no |
| machine | Target Juju machine to deploy on | `string` | n/a | no |
| units | Number of units to deploy | `number` | `1` | no |
| constraints | Juju constraints to apply for this application | `string` | `arch=amd64` | no |
| storage | Storage directive | `map(string)` | `{}` | no |
| config | Application configuration. Details at https://charmhub.io/postgresql/configurations | `map(string)` | `{}` | no |
| enable_expose | Whether to expose the application | `bool` | `true` | no |

## Outputs

| Name | Description |
|------|-------------|
| application | The full `juju_application` resource which makes up this product module |
| application_name | Application name which make up this product module |
| provides | Endpoints charm provides |
| requires | Endpoints charm requires |

