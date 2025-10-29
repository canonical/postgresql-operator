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

## Update keys

Updates to private keys for certificate signing requests (CSR) can be made via the `set-tls-private-key` action. Note that passing keys to external/internal keys should *only be done with* `base64 -w0`, *not* `cat`. 

With three replicas, this schema should be followed:

Generate a shared internal key:

```text
openssl genrsa -out internal-key.pem 3072
```

Generate external keys for each unit:

```text
openssl genrsa -out external-key-0.pem 3072
openssl genrsa -out external-key-1.pem 3072
openssl genrsa -out external-key-2.pem 3072
```

Apply both private keys to each unit. The shared internal key will be applied only to the juju leader.

```text
juju run postgresql/0 set-tls-private-key "external-key=$(base64 -w0 external-key-0.pem)"  "internal-key=$(base64 -w0 internal-key.pem)"  --wait
juju run postgresql/1 set-tls-private-key "external-key=$(base64 -w0 external-key-1.pem)"  "internal-key=$(base64 -w0 internal-key.pem)"  --wait
juju run postgresql/2 set-tls-private-key "external-key=$(base64 -w0 external-key-2.pem)"  "internal-key=$(base64 -w0 internal-key.pem)"  --wait
```

Updates can also be done with auto-generated keys:

```text
juju run postgresql/0 set-tls-private-key
juju run postgresql/1 set-tls-private-key
juju run postgresql/2 set-tls-private-key
```

## Disable TLS

Disable TLS by removing the integration.

```text
juju remove-relation postgresql:client-certificates self-signed-certificates:certificates
```

