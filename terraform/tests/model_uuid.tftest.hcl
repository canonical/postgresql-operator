mock_provider "juju" {}

run "model_uuid_is_used_when_set" {
  command = plan

  variables {
    model_uuid = "11111111-2222-4333-8444-555555555555"
  }

  assert {
    condition     = juju_application.machine_postgresql.model_uuid == "11111111-2222-4333-8444-555555555555"
    error_message = "model_uuid must be forwarded to the application"
  }
}

run "deprecated_juju_model_still_works" {
  command = plan

  variables {
    juju_model = "11111111-2222-4333-8444-555555555555"
  }

  expect_failures = [check.juju_model_deprecated]

  assert {
    condition     = juju_application.machine_postgresql.model_uuid == "11111111-2222-4333-8444-555555555555"
    error_message = "juju_model must keep working as a fallback for model_uuid"
  }
}

run "both_inputs_with_the_same_value_are_accepted" {
  command = plan

  variables {
    model_uuid = "11111111-2222-4333-8444-555555555555"
    juju_model = "11111111-2222-4333-8444-555555555555"
  }

  expect_failures = [check.juju_model_deprecated]

  assert {
    condition     = juju_application.machine_postgresql.model_uuid == "11111111-2222-4333-8444-555555555555"
    error_message = "setting both inputs to the same value must be accepted"
  }
}

run "conflicting_model_inputs_are_rejected" {
  command = plan

  variables {
    model_uuid = "11111111-2222-4333-8444-555555555555"
    juju_model = "99999999-8888-4777-8666-555555555555"
  }

  expect_failures = [
    check.juju_model_deprecated,
    juju_application.machine_postgresql,
  ]
}

run "missing_model_is_rejected" {
  command = plan

  expect_failures = [juju_application.machine_postgresql]
}
