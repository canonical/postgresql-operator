# Juju tech details

[Juju](https://juju.is/) is an open source orchestration engine for software operators that enables the deployment, integration and lifecycle management of applications at any scale, on any infrastructure using charms.

This [charm](https://charmhub.io/postgresql) is an operator - business logic encapsulated in reusable software packages that automate every aspect of an application's life. Charms are shared via [CharmHub](https://charmhub.io/).

See also:

* [Juju Documentation](https://juju.is/docs/juju) and [Blog](https://ubuntu.com/blog/tag/juju)
* [Charm SDK](https://juju.is/docs/sdk)

## Breaking changes between Juju 2.9.x and 3.x

As this charm documentation is written for Juju 3.x, users of 2.9.x will encounter noteworthy changes when following the instructions. This section explains those changes.

Breaking changes have been introduced in the Juju client between versions 2.9.x and 3.x. These are caused by the renaming and re-purposing of several commands - functionality and command options remain unchanged.

In the context of this guide, the pertinent changes are shown here:

|2.9.x|3.x|
| --- | --- |
|**add-relation**|**integrate**|
|**relate**|**integrate**|
|**run**|**exec**|
|**run-action --wait**|**run**|

See the [Juju 3.0 release notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022) for the comprehensive list of changes.

The response is to therefore substitute the documented command with the equivalent 2.9.x command. For example:

### Juju 3.x:
```shell
juju integrate postgresql:database postgresql-test-app

juju run postgresql/leader get-password 
```
### Juju 2.9.x:
```shell
juju relate postgresql:database postgresql-test-app

juju run-action --wait postgresql/leader get-password
```
> :tipping_hand_man: [The document based on OpenStack guide.](https://docs.openstack.org/charm-guide/latest/project/support-notes.html#breaking-changes-between-juju-2-9-x-and-3-x)