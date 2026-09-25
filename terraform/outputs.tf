output "application" {
  value = juju_application.machine_postgresql
}

output "application_name" {
  value = juju_application.machine_postgresql.name
}

output "provides" {
  value = {
    database          = "database"
    db                = "db"
    db_admin          = "db-admin"
    cos_agent         = "cos-agent"
    replication_offer = "replication-offer"
  }
}

output "requires" {
  value = {
    replication     = "replication"
    certificates    = "certificates"
    receive_ca_cert = "receive-ca-cert"
    s3_parameters   = "s3-parameters"
    ldap            = "ldap"
    tracing         = "tracing"
  }
}

