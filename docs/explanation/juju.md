# Juju

[Juju](https://juju.is/) is an open source orchestration engine for software operators that enables the deployment, integration and lifecycle management of applications at any scale, on any infrastructure using charms.

> See also: [Juju client documentation](https://juju.is/docs/juju), [Juju blog](https://ubuntu.com/blog/tag/juju)

## Compatibility with PostgreSQL

Current stable releases of this charm can still be deployed on Juju 2.9. However, newer features are not supported.
> See the [Releases page](/reference/releases) for more information about the minimum Juju version required to operate the features of each revision. 

Additionally, there are limitations regarding integrations with other charms. For example, integration with  [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates) requires Juju 3.x.

## Breaking changes between Juju 2.9.x and 3.x

As this charm documentation is written for Juju 3.x, users of 2.9.x will encounter noteworthy changes when following the instructions. This section explains those changes.

Breaking changes have been introduced in the Juju client between versions 2.9.x and 3.x. These are caused by the renaming and re-purposing of several commands - functionality and command options remain unchanged.

In the context of this documentation, the pertinent changes are as follows:

| v2.9.x | v3.x |
| --- | --- |
|`add-relation`|`integrate`|
|`relate`|`integrate`|
|`run`|`exec`|
|`run-action --wait`|`run`|

See the [Juju 3.0 release notes](https://documentation.ubuntu.com/juju/3.6/reference/juju/juju-roadmap-and-releases/#juju-3-0-0-22-oct-2022) for the comprehensive list of changes.

Example substitutions:

### Juju 3.x:

```text
juju integrate postgresql:database postgresql-test-app

juju run postgresql/leader get-password 
```
### Juju 2.9.x:

```text
juju relate postgresql:database postgresql-test-app

juju run-action --wait postgresql/leader get-password
```
> This document is based on [this OpenStack guide](https://docs.openstack.org/charm-guide/latest/project/support-notes.html#breaking-changes-between-juju-2-9-x-and-3-x)

