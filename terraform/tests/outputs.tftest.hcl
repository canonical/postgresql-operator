mock_provider "juju" {}

variables {
  model_uuid = "11111111-2222-4333-8444-555555555555"
}

run "endpoint_outputs_are_stable" {
  command = plan

  assert {
    condition     = output.provides == { database = "database", cos_agent = "cos-agent" }
    error_message = "provides must expose exactly the database and cos-agent endpoints"
  }

  assert {
    condition     = output.requires == { certificates = "certificates", s3_parameters = "s3-parameters" }
    error_message = "requires must expose exactly the certificates and s3-parameters endpoints"
  }
}

run "application_name_output_follows_app_name" {
  command = plan

  variables {
    app_name = "pg-custom"
  }

  assert {
    condition     = output.application_name == "pg-custom"
    error_message = "application_name must reflect the app_name input"
  }
}
