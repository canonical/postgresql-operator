locals {
  # Prefer the CC008 `model_uuid` input; the deprecated `juju_model` input is
  # kept for backwards compatibility and used only when `model_uuid` is unset.
  model_uuid = var.model_uuid != null ? var.model_uuid : var.juju_model
}

check "juju_model_deprecated" {
  assert {
    condition     = var.juju_model == null
    error_message = "The juju_model input is deprecated and will be removed in a future release; use model_uuid instead."
  }
}

resource "juju_application" "machine_postgresql" {
  name       = var.app_name
  model_uuid = local.model_uuid

  charm {
    name     = "postgresql"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  storage_directives = {
    pgdata = var.storage_size
  }

  units       = var.units
  constraints = var.constraints
  config      = var.config

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
