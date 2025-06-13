resource "juju_application" "machine_postgresql" {
  name  = var.app_name
  model = var.juju_model_name

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
}
