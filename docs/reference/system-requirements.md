# System requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on VM.

## Software
* Ubuntu 24.04 (Noble) or later.

### Juju

Charmed PostgreSQL 16 supports several Juju releases from 3.6 LTS onwards. The table below shows which minor versions of each major Juju release are supported by the stable Charmhub releases of PostgreSQL.

| Juju major release | Supported minor versions | Compatible charm revisions |Comment |
|:--------|:-----|:-----|:-----|
| ![3.6 LTS] | `3.6.1+` |  | |


## Hardware

Make sure your machine meets the following requirements:

* 8GB of RAM.
* 2 CPU threads.
* At least 20GB of available storage.

The charm is based on the [charmed-postgresql snap](https://snapcraft.io/charmed-postgresql). It currently supports:
* `amd64`
* `arm64`

[Contact us](/reference/contacts) if you are interested in a new architecture!

## Networking
* Access to the internet is required for downloading required snaps and charms
* Only IPv4 is supported at the moment
  * See more information about this limitation in [this Jira issue](https://warthogs.atlassian.net/browse/DPE-4695)
  * [Contact us](/reference/contacts) if you are interested in IPv6!


<!-- BADGES -->

[3.6 LTS]: https://img.shields.io/badge/3.6_LTS-%23E95420?label=Juju

<!-- LINKS -->
[552]: https://github.com/canonical/postgresql-operator/releases/tag/rev552
[288]: https://github.com/canonical/postgresql-operator/releases/tag/rev288
[336]: https://github.com/canonical/postgresql-operator/releases/tag/rev336
[363]: https://github.com/canonical/postgresql-operator/releases/tag/rev363
[430]: https://github.com/canonical/postgresql-operator/releases/tag/rev429

