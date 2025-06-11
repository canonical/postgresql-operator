# Cryptography

This document describes the cryptography used by Charmed PostgreSQL.

## Resource checksums

Charmed PostgreSQL and Charmed PgBouncer operators use pinned versions of the respective snaps to provide reproducible and secure environments.

The snaps package their workloads along with the necessary dependencies and utilities required for the operators’ lifecycle. For more details, see the snaps content in the `snapcraft.yaml` file for [PostgreSQL](https://github.com/canonical/charmed-postgresql-snap/blob/14/edge/snap/snapcraft.yaml) and [PgBouncer](https://github.com/canonical/charmed-pgbouncer-snap/blob/1/edge/snap/snapcraft.yaml).

Every artifact bundled into a snap is verified against its MD5, SHA256, or SHA512 checksum after download. The installation of certified snap into the rock is ensured by snap primitives that verify their squashfs filesystems images GPG signature. For more information on the snap verification process, refer to the [snapcraft.io documentation](https://snapcraft.io/docs/assertions).

## Sources verification

PostgreSQL and its extra components are built by Canonical from upstream source codes on [Launchpad](https://launchpad.net/ubuntu/+source/postgresql-common). PostgreSQL and PgBouncer are built as deb packages, other components - as PPAs.

Charmed PostgreSQL and Charmed PgBouncer charms and snaps are published and released programmatically using release pipelines implemented via GitHub Actions in their respective repositories.

All repositories in GitHub are set up with branch protection rules, requiring:

* new commits to be merged to main branches via pull request with at least 2 approvals from repository maintainers
* new commits to be signed (e.g. using GPG keys)
* developers to sign the [Canonical Contributor License Agreement (CLA)](https://ubuntu.com/legal/contributors)

## Encryption

Charmed PostgreSQL can be used to deploy a secure PostgreSQL cluster that provides encryption-in-transit capabilities out of the box for:

* Cluster internal communications
* PgBouncer connections
* External clients connections

To set up a secure connection Charmed PostgreSQL and Charmed PgBouncer need to be integrated with TLS Certificate Provider charms, e.g. self-signed-certificates operator. Certificate Signing Requests (CSRs) are generated for every unit using the tls_certificates_interface library that uses the cryptography Python library to create X.509 compatible certificates. The CSR is signed by the TLS Certificate Provider, returned to the units, and stored in Juju secret. The relation also provides the CA certificate, which is loaded into Juju secret.

Encryption at rest is currently not supported, although it can be provided by the substrate (cloud or on-premises).

## Authentication

In Charmed PostgreSQL, authentication layers can be enabled for:

1. PgBouncer authentication to PostgreSQL
2. PostgreSQL cluster authentication
3. Clients authentication to PostgreSQL

### PgBouncer authentication to PostgreSQL

Authentication of PgBouncer to PostgreSQL is based on the password-based `scram-sha-256` authentication method. See the [PostgreSQL official documentation](https://www.postgresql.org/docs/14/auth-password.html) for more details.

Credentials are exchanged via [Juju secrets](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-secrets/).

### PostgreSQL cluster authentication

Authentication among members of a PostgreSQL cluster is based on the password-based `scram-sha-256` authentication method.

An internal user is used for this authentication with its hashed password stored in a system metadata database. These credentials are also stored as a plain text file on the disk of each unit for the Patroni HA service.

### Clients authentication to PostgreSQL

Authentication of clients to PostgreSQL is based on the password-based `scram-sha-256` authentication method. See the [PostgreSQL official documentation](https://www.postgresql.org/docs/14/auth-password.html) for more details.

Credentials are exchanged via [Juju secrets](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-secrets/).

