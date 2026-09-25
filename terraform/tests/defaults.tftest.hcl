mock_provider "juju" {}

variables {
  model_uuid = "11111111-2222-4333-8444-555555555555"
}

run "defaults_reach_the_application" {
  command = plan

  assert {
    condition     = juju_application.machine_postgresql.name == "postgresql"
    error_message = "app_name must default to \"postgresql\""
  }

  assert {
    condition     = juju_application.machine_postgresql.charm[0].name == "postgresql"
    error_message = "charm_name must default to \"postgresql\""
  }

  assert {
    condition     = juju_application.machine_postgresql.charm[0].channel == "14/stable"
    error_message = "channel must default to \"14/stable\""
  }

  assert {
    condition     = juju_application.machine_postgresql.charm[0].base == "ubuntu@22.04"
    error_message = "base must default to \"ubuntu@22.04\""
  }

  assert {
    condition     = var.revision == null
    error_message = "revision must default to null so the channel decides the revision"
  }

  assert {
    condition     = juju_application.machine_postgresql.constraints == "arch=amd64"
    error_message = "constraints must default to \"arch=amd64\""
  }

  assert {
    condition     = length(juju_application.machine_postgresql.expose) == 1
    error_message = "enable_expose must default to true, producing exactly one expose block"
  }

  assert {
    condition     = juju_application.machine_postgresql.storage_directives == tomap({ pgdata = "10G" })
    error_message = "storage must default to a 10G pgdata storage directive"
  }
}

run "expose_can_be_disabled" {
  command = plan

  variables {
    enable_expose = false
  }

  assert {
    condition     = length(juju_application.machine_postgresql.expose) == 0
    error_message = "enable_expose = false must produce no expose block"
  }
}

run "config_and_storage_are_passed_through" {
  command = plan

  variables {
    app_name    = "pg-custom"
    channel     = "14/edge"
    constraints = "arch=arm64 mem=4G"
    config      = { profile = "testing", plugin_hstore_enable = "true" }
    storage     = { pgdata = "20G" }
  }

  assert {
    condition     = juju_application.machine_postgresql.name == "pg-custom"
    error_message = "app_name must be used as the application name"
  }

  assert {
    condition     = juju_application.machine_postgresql.charm[0].channel == "14/edge"
    error_message = "channel must be passed through to the charm block"
  }

  assert {
    condition     = juju_application.machine_postgresql.constraints == "arch=arm64 mem=4G"
    error_message = "constraints must be passed through unmodified"
  }

  assert {
    condition     = juju_application.machine_postgresql.config["profile"] == "testing"
    error_message = "config must be passed through unmodified"
  }

  assert {
    condition     = juju_application.machine_postgresql.storage_directives["pgdata"] == "20G"
    error_message = "storage must be passed through as storage_directives"
  }
}

run "pinned_revision_is_passed_through" {
  command = plan

  variables {
    revision = 693
  }

  assert {
    condition     = juju_application.machine_postgresql.charm[0].revision == 693
    error_message = "revision must be forwarded to the charm block when pinned"
  }
}
