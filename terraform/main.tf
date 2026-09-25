locals {
  # Prefer the CC008 `model_uuid` input; the deprecated `juju_model` input is
  # kept for backwards compatibility and used only when `model_uuid` is unset.
  model_uuid = var.model_uuid != null ? var.model_uuid : var.juju_model

  # The deprecated `storage_size` input is kept for backwards compatibility and,
  # when set, overrides the `pgdata` entry of the `storage` input.
  storage = var.storage_size != null ? merge(var.storage, { pgdata = var.storage_size }) : var.storage
}

check "juju_model_deprecated" {
  assert {
    condition     = var.juju_model == null
    error_message = "The juju_model input is deprecated and will be removed in a future release; use model_uuid instead."
  }
}

check "storage_size_deprecated" {
  assert {
    condition     = var.storage_size == null
    error_message = "The storage_size input is deprecated and will be removed in a future release; use storage = { pgdata = \"<size>\" } instead."
  }
}

resource "juju_application" "machine_postgresql" {
  name = var.app_name

  charm {
    name     = var.charm_name
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  machines           = var.machines != null ? var.machines : (var.machine != null ? [var.machine] : null)
  units              = (var.machines == null && var.machine == null) ? var.units : null
  config             = var.config
  constraints        = var.constraints
  storage_directives = local.storage
  model_uuid         = local.model_uuid

  dynamic "expose" {
    for_each = var.enable_expose ? [1] : []
    content {}
  }

  lifecycle {
    precondition {
      condition     = var.model_uuid == null || var.juju_model == null || var.model_uuid == var.juju_model
      error_message = "Both model_uuid and juju_model are set with different values; set only model_uuid."
    }

    precondition {
      condition     = local.model_uuid != null
      error_message = "Set model_uuid to the UUID of the Juju model to deploy into."
    }
  }
}
