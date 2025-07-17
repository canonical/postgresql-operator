resource "juju_application" "machine_postgresql" {
  name  = var.app_name
  model = var.juju_model_name

  charm {
    name     = var.charm_name
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  machines           = var.machine != null ? [var.machine] : null
  units              = var.machine == null ? var.units : null
  config             = var.config
  constraints        = var.constraints
  storage_directives = var.storage

  dynamic "expose" {
    for_each = var.enable_expose ? [1] : []
    content {}
  }
}
