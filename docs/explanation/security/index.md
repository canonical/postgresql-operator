# Security hardening guide

This document provides an overview of security features and guidance for hardening the security of [Charmed PostgreSQL](https://charmhub.io/postgresql) deployments, including setting up and managing a secure environment.

## Environment

The environment where Charmed PostgreSQL operates can be divided into two components:

1. Cloud
2. Juju

### Cloud

Charmed PostgreSQL can be deployed on top of several clouds and virtualisation layers:

|Cloud|Security guides|
| --- | --- |
|OpenStack|[OpenStack Security Guide](https://docs.openstack.org/security-guide/)|
|AWS|[Best Practices for Security, Identity and Compliance](https://aws.amazon.com/architecture/security-identity-compliance), [AWS security credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds.html)|
|Azure|[Azure security best practices and patterns](https://learn.microsoft.com/en-us/azure/security/fundamentals/best-practices-and-patterns), [Managed identities for Azure resource](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/)|
|GCP|[Google security overview](https://cloud.google.com/docs/security)|

### Juju

Juju is the component responsible for orchestrating the entire lifecycle, from deployment to Day 2 operations. For more information on Juju security hardening, see the [Juju security page](https://canonical-juju.readthedocs-hosted.com/en/latest/user/explanation/juju-security/) and the [How to harden your deployment](https://juju.is/docs/juju/harden-your-deployment) guide.

#### Cloud credentials

When configuring cloud credentials to be used with Juju, ensure that users have correct permissions to operate at the required level. Juju superusers responsible for bootstrapping and managing controllers require elevated permissions to manage several kinds of resources, such as virtual machines, networks, storage, etc. Please refer to the links below for more information on the policies required to be used depending on the cloud.

|Cloud|Cloud user policies|
| --- | --- |
|OpenStack|N/A|
|AWS|[Juju AWS Permission](https://discourse.charmhub.io/t/juju-aws-permissions/5307), [AWS Instance Profiles](https://discourse.charmhub.io/t/using-aws-instance-profiles-with-juju-2-9/5185), [Juju on AWS](https://juju.is/docs/juju/amazon-ec2)|
|Azure|[Juju Azure Permission](https://juju.is/docs/juju/microsoft-azure), [How to use Juju with Microsoft Azure](https://discourse.charmhub.io/t/how-to-use-juju-with-microsoft-azure/15219)|
|GCP|[Google Cloud's Identity and Access Management](https://cloud.google.com/iam/docs/overview), [GCE role recommendations](https://cloud.google.com/policy-intelligence/docs/role-recommendations-overview), [Google GCE cloud and Juju](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/cloud/list-of-supported-clouds/the-google-gce-cloud-and-juju/)|

#### Juju users

It is very important that Juju users are set up with minimal permissions depending on the scope of their operations. Please refer to the [User access levels](https://juju.is/docs/juju/user-permissions) documentation for more information on the access levels and corresponding abilities.

Juju user credentials must be stored securely and rotated regularly to limit the chances of unauthorised access due to credentials leakage.

## Applications

In the following sections, we provide guidance on how to harden your deployment using:

1. Operating system
2. Security upgrades
3. Encryption
4. Authentication
5. Monitoring and auditing

### Operating system

Charmed PostgreSQL and Charmed PgBouncer run on top of Ubuntu 22.04. Deploy a [Landscape Client Charm](https://charmhub.io/landscape-client?) to connect the underlying VM to a Landscape User Account to manage security upgrades and integrate [Ubuntu Pro](https://ubuntu.com/pro) subscriptions.

### Security upgrades

[Charmed PostgreSQL](https://charmhub.io/postgresql) and [Charmed PgBouncer](https://charmhub.io/pgbouncer) operators install pinned versions of their respective snaps to provide reproducible and secure environments.

New versions (revisions) of the charmed operators can be released to update the operator's code, workloads, or both. It is important to refresh the charms regularly to make sure the workloads are as secure as possible.

For more information on upgrading Charmed PostgreSQL, see the [How to upgrade PostgreSQL](https://canonical.com/data/docs/postgresql/iaas/h-upgrade) and [How to upgrade PgBouncer](https://charmhub.io/pgbouncer/docs/h-upgrade) guides, as well as the respective Release notes for [PostgreSQL](https://canonical.com/data/docs/postgresql/iaas/r-releases) and [PgBouncer](https://charmhub.io/pgbouncer/docs/r-releases).

### Encryption

To utilise encryption at transit for all internal and external cluster connections, integrate Charmed PostgreSQL with a TLS certificate provider. Please refer to the [Charming Security page](https://charmhub.io/topics/security-with-x-509-certificates) for more information on how to select the right certificate provider for your use case.

Encryption in transit for backups is provided by the storage service (Charmed PostgreSQL is a client for an S3-compatible storage).

For more information on encryption, see the [Cryptography](/explanation/security/cryptography) explanation page and [How to enable encryption](https://canonical.com/data/docs/postgresql/iaas/h-enable-tls) guide.

### Authentication

Charmed PostgreSQL supports the password-based `scram-sha-256` authentication method for authentication between:

* External connections to clients
* Internal connections between members of cluster
* PgBouncer connections

For more implementation details, see the [PostgreSQL documentation](https://www.postgresql.org/docs/14/auth-password.html).

### Monitoring and auditing

Charmed PostgreSQL provides native integration with the [Canonical Observability Stack (COS)](https://charmhub.io/topics/canonical-observability-stack). To reduce the blast radius of infrastructure disruptions, the general recommendation is to deploy COS and the observed application into separate environments, isolated from one another. Refer to the [COS production deployments best practices](https://charmhub.io/topics/canonical-observability-stack/reference/best-practices) for more information or see the How to guides for PostgreSQL [monitoring](https://canonical.com/data/docs/postgresql/iaas/h-enable-monitoring), [alert rules](https://canonical.com/data/docs/postgresql/iaas/h-enable-alert-rules), and [tracing](https://canonical.com/data/docs/postgresql/iaas/h-enable-tracing) for practical instructions.

PostgreSQL logs are stored in `/var/snap/charmed-postgresql/common/var/log/postgresql` within the PostgreSQL container of each unit. It’s recommended to integrate the charm with [COS](/how-to/monitoring-cos/enable-monitoring), from where the logs can be easily persisted and queried using [Loki](https://charmhub.io/loki-k8s)/[Grafana](https://charmhub.io/grafana).

## Additional resources

For details on the cryptography used by Charmed PostgreSQL, see the [Cryptography](/explanation/security/cryptography) explanation page.


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Cryptography <cryptography>
