# How to enable LDAP authentication

The Lightweight Directory Access Protocol (LDAP) enables centralised authentication for PostgreSQL clusters, reducing the overhead of managing local credentials and access policies.

This guide goes over the steps to integrate LDAP as an authentication method with the PostgreSQL charm, all within the Juju ecosystem.

## Prerequisites

* Charmed PostgreSQL revision `600+`
* Juju `3.6+`

## Deploy an LDAP server in a K8s environment

```{caution}
In this guide, we use [self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) provided by the [`self-signed-certificates` operator](https://github.com/canonical/self-signed-certificates-operator). 

**This is not recommended for a production environment.**

For production environments, check the collection of [Charmhub operators](https://charmhub.io/?q=tls-certificates) that implement the `tls-certificate` interface, and choose the most suitable for your use-case.
```

Switch to a Kubernetes controller:

```text
juju switch <k8s_controller>
```

Deploy the [GLAuth charm](https://charmhub.io/glauth-k8s):

```text
juju add-model glauth
juju deploy self-signed-certificates
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy glauth-k8s --channel edge --trust
```

Integrate (formerly known as "relate") the three applications:

```text
juju integrate glauth-k8s:certificates self-signed-certificates
juju integrate glauth-k8s:pg-database postgresql-k8s
```

Deploy the [GLAuth-utils charm](https://charmhub.io/glauth-utils), in order to manage LDAP users:

```text
juju deploy glauth-utils --channel edge --trust
```

Integrate (formerly known as "relate") the two applications:

```text
juju integrate glauth-k8s glauth-utils
```

## Expose cross-controller URLs

Enable the required micro-k8s plugin:

```text
IPADDR=$(ip -4 -j route get 2.2.2.2 | jq -r '.[] | .prefsrc')
sudo microk8s enable metallb $IPADDR-$IPADDR
```

Deploy the [Traefik charm](https://charmhub.io/traefik-k8s), in order to expose endpoints from the K8s cluster:

```text
juju deploy traefik-k8s --trust
```

Integrate (formerly known as "relate") the two applications:

```text
juju integrate glauth-k8s:ingress traefik-k8s
```

## Expose cross-model relations

To offer the GLAuth interfaces, run:

```text
juju offer glauth-k8s:ldap ldap
juju offer glauth-k8s:send-ca-cert send-ca-cert
```

## Enable LDAP

Switch to the VM controller:

```text
juju switch <lxd_controller>:postgresql
```

To have LDAP offers consumed:

```text
juju consume <k8s_controller>:admin/glauth.ldap
juju consume <k8s_controller>:admin/glauth.send-ca-cert
```

To have LDAP authentication enabled, integrate the PostgreSQL charm with the GLAuth charm:

```text
juju integrate postgresql:ldap ldap
juju integrate postgresql:receive-ca-cert send-ca-cert
```

## Map LDAP users to PostgreSQL

To have LDAP users available in PostgreSQL, provide a comma separated list of LDAP groups to already created PostgreSQL authorisation groups. To create those groups before hand, refer to the [data integrator charm](https://charmhub.io/data-integrator).

```text
juju config postgresql ldap_map="<ldap_group>=<psql_group>"
```

## Disable LDAP

You can disable LDAP removing the following relations:

```text
juju remove-relation postgresql.receive-ca-cert send-ca-cert
juju remove-relation postgresql.ldap ldap
```

