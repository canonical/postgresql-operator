output "application" {
  value = juju_application.machine_postgresql
}

output "application_name" {
  value = juju_application.machine_postgresql.name
}

output "provides" {
  value = {
    database          = "database"
    cos_agent         = "cos-agent"
    replication_offer = "replication-offer"
    watcher_offer     = "watcher-offer"
  }
}

output "requires" {
  value = {
    replication         = "replication"
    peer_certificates   = "peer-certificates"
    client_certificates = "client-certificates"
    receive_ca_cert     = "receive-ca-cert"
    s3_parameters       = "s3-parameters"
    ldap                = "ldap"
    tracing             = "tracing"
  }
}

