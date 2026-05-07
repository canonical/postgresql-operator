(enable-tls)=
# How to enable TLS encryption

This guide will show how to enable TLS/SSL on a PostgreSQL cluster using the [`self-signed-certificates` operator](https://github.com/canonical/self-signed-certificates-operator) as an example.

This guide assumes everything is deployed within the same network and Juju model.

> See also: [](/how-to/deploy/tls-vip-access)

## Enable TLS

```{caution}
**[Self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) are not recommended for a production environment.**

Check [this guide about X.509 certificates](https://discourse.charmhub.io/t/security-with-x-509-certificates/11664) for an overview of all the TLS certificate charms available. 
```

First, deploy the TLS charm:

```text
juju deploy self-signed-certificates
```

To enable TLS integrate (formerly known as “relate”) the two applications:

```text
juju integrate postgresql:client-certificates self-signed-certificates:certificates
```

## Check certificates in use

To check the certificates in use by PostgreSQL, run

```text
openssl s_client -starttls postgres -connect <leader_unit_IP>:<port> | grep issuer
```

## Disable TLS

Disable TLS by removing the integration.

```text
juju remove-relation postgresql:client-certificates self-signed-certificates:certificates
```

