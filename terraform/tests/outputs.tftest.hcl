mock_provider "juju" {}

variables {
  model_uuid = "11111111-2222-4333-8444-555555555555"
}

run "endpoint_outputs_are_stable" {
  command = plan

  assert {
    condition     = toset(values(output.provides)) == toset(keys(yamldecode(file("${path.module}/../metadata.yaml")).provides))
    error_message = "provides must match the provides endpoints in metadata.yaml"
  }


  assert {
    condition     = toset(values(output.requires)) == toset(keys(yamldecode(file("${path.module}/../metadata.yaml")).requires))
    error_message = "requires must match the requires endpoints in metadata.yaml"
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
