mock_provider "juju" {}

variables {
  model_uuid = "11111111-2222-4333-8444-555555555555"
}

run "deprecated_storage_size_still_works" {
  command = plan

  variables {
    storage_size = "20G"
  }

  expect_failures = [check.storage_size_deprecated]

  assert {
    condition     = juju_application.machine_postgresql.storage_directives == tomap({ pgdata = "20G" })
    error_message = "storage_size must keep setting the pgdata storage directive"
  }
}

run "deprecated_storage_size_overrides_storage_pgdata" {
  command = plan

  variables {
    storage      = { pgdata = "30G" }
    storage_size = "20G"
  }

  expect_failures = [check.storage_size_deprecated]

  assert {
    condition     = juju_application.machine_postgresql.storage_directives["pgdata"] == "20G"
    error_message = "storage_size must take precedence over the pgdata entry of storage"
  }
}
