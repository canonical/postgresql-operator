# System requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on VM.

## Software
* Ubuntu 22.04 (Jammy) or later.

The minimum supported Juju versions are:

* 2.9.32+ (older versions are untested).
* 3.1.7+ (Juju secrets were stabilized in `v.3.1.7`)

[note type="caution"]
**Note**: Juju 3.1 is supported from the charm revision 315+
[/note]

## Hardware

Make sure your machine meets the following requirements:

* 8GB of RAM.
* 2 CPU threads.
* At least 20GB of available storage.

The charm is based on the [charmed-postgresql snap](https://snapcraft.io/charmed-postgresql). It currently supports:
* `amd64`
* `arm64` (from revision 396+)

[Contact us](/t/11863) if you are interested in a new architecture!

## Networking
* Access to the internet is required for downloading required snaps and charms
* Only IPv4 is supported at the moment
  * See more information about this limitation in [this Jira issue](https://warthogs.atlassian.net/browse/DPE-4695)
  * [Contact us](/t/11863) if you are interested in IPv6!