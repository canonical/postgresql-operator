mock_provider "juju" {}

variables {
  model_uuid = "11111111-2222-4333-8444-555555555555"
}

run "machine_placement_sets_machines_and_drops_units" {
  command = plan

  variables {
    machine = "3"
    units   = 5
  }

  assert {
    condition     = juju_application.machine_postgresql.machines == toset(["3"])
    error_message = "machine must be turned into a single-element machines set"
  }
}

run "without_machine_units_is_used" {
  command = plan

  variables {
    units = 3
  }

  assert {
    condition     = juju_application.machine_postgresql.units == 3
    error_message = "units must be forwarded when no machine is targeted"
  }
}

run "default_unit_count_is_one" {
  command = plan

  assert {
    condition     = juju_application.machine_postgresql.units == 1
    error_message = "units must default to 1"
  }
}
