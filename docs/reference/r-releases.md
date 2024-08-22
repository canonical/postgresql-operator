# Release Notes
Here you will find release notes for major revisions of this charm that are available in the [Charmhub `stable` channel](https://juju.is/docs/juju/channel#heading--risk).

To see  **all** charm revisions, check the [Charmed PostgreSQL Releases page](https://github.com/canonical/postgresql-operator/releases) on GitHub.

## At a glance

The table below is a high-level overview of the architectures and integrations that are supported by each charm revision. 

| Revision | amd64 | arm64 | [TLS  encryption](/t/9685)* | [Monitoring (COS, Grafana)](/t/10600)  | [Tracing (Tempo K8s)](/t/14521)  |
|:--------:|:-----:|:-----:|:--------------------:|:---------------:|:--------------------:|
| [430](/t/14067) |  | ![check] | ![check] | ![check] | ![check] |
| [429](/t/14067) | ![check] |  | ![check] | ![check] | ![check] |
| [363](/t/13124) | ![check] |  | ![check] | ![check] |  |
| [351](/t/12823) | ![check] |  |  | ![check] |  |
| [336](/t/11877) | ![check] |  |  | ![check] |  |
| [288](/t/11876) | ![check] |  |  |  |  |



**TLS encryption***: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

For more details about a particular revision, refer to its dedicated Release Notes page.
For more details about each feature/interface, refer to their dedicated How-To guide.

### Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/t/10946).

<!-- BADGES -->
[check]: https://img.shields.io/badge/%E2%9C%93-brightgreen
[cross]: https://img.shields.io/badge/x-white