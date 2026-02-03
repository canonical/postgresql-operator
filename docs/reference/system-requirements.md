# System requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on VM.

## Software
* Ubuntu 22.04 (Jammy) or later.

### Juju

The charm supports several Juju releases from [2.9 LTS](https://documentation.ubuntu.com/juju/3.6/releasenotes/juju_2.9.x/) onwards. The table below shows which minor versions of each major Juju release are supported by the stable Charmhub releases of PostgreSQL.

| Juju major release | Supported minor versions | Compatible charm revisions |Comment |
|:--------|:-----|:-----|:-----|
| ![3.6 LTS] | `3.6.1+` | [552]+ | `3.6.0` is not recommended, while `3.6.1+` works excellent. Recommended for production!  |
| [![3.5]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x//#juju-3-5) | `3.5.1+` | [363]+  | [Known Juju issue](https://bugs.launchpad.net/juju/+bug/2066517) in `3.5.0` |
| [![3.4]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x//#juju-3-4) | `3.4.3+` | [363]+ | Know Juju issues with previous minor versions |
| [![3.3]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x//#juju-3-3) | `3.3.0+` | from [363] to [430] | No known issues |
| [![3.2]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x//#juju-3-2) | `3.2.0+` | from [363] to [430]  | No known issues |
| [![3.1]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x/#juju-3-1) | `3.1.7+` | from [336] to [430] | Juju secrets were stabilised in `3.1.7` |
| [![2.9 LTS]](https://documentation.ubuntu.com/juju/3.6/releasenotes/juju_2.9.x/)  | `2.9.49+` | [288]+ | |
|  | `2.9.32+` | from [288] to [430] | No tests for older Juju versions. |

## Hardware

Make sure your machine meets the following requirements:

* 8GB of RAM.
* 2 CPU threads.
* At least 20GB of available storage.

The charm is based on the [charmed-postgresql snap](https://snapcraft.io/charmed-postgresql). It currently supports:
* `amd64`
* `arm64` (from revision 396+)

[Contact us](/reference/contacts) if you are interested in a new architecture!

## Networking

* Access to the internet is required for downloading required snaps and charms
* Only IPv4 is supported at the moment
  * See more information about this limitation in [this Jira issue](https://warthogs.atlassian.net/browse/DPE-4695)
  * [Contact us](/reference/contacts) if you are interested in IPv6!


<!-- BADGES -->

[2.9 LTS]: https://img.shields.io/badge/2.9_LTS-%23E95420?label=Juju
[3.1]: https://img.shields.io/badge/3.1-%23E95420?label=Juju
[3.2]: https://img.shields.io/badge/3.2-%23E95420?label=Juju
[3.3]: https://img.shields.io/badge/3.3-%23E95420?label=Juju
[3.4]: https://img.shields.io/badge/3.4-%23E95420?label=Juju
[3.5]: https://img.shields.io/badge/3.5-%23E95420?label=Juju
[3.6 LTS]: https://img.shields.io/badge/3.6_LTS-%23E95420?label=Juju

<!-- LINKS -->
[552]: https://github.com/canonical/postgresql-operator/releases/tag/rev552
[288]: https://github.com/canonical/postgresql-operator/releases/tag/rev288
[336]: https://github.com/canonical/postgresql-operator/releases/tag/rev336
[363]: https://github.com/canonical/postgresql-operator/releases/tag/rev363
[430]: https://github.com/canonical/postgresql-operator/releases/tag/rev429

