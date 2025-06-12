# Contributing

## Overview

This documents explains the processes and practices recommended for contributing enhancements to
this operator.

- Generally, before developing enhancements to this charm, you should consider [opening an issue
  ](https://github.com/canonical/postgresql-operator/issues) explaining your use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach
  us using any channel from our [Contacts](https://charmhub.io/postgresql/docs/r-contacts).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library
  will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators of this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto
  the `main` branch. This also avoids merge commits and creates a linear Git commit history.

## Developing

You can create an environment for development with `tox`:

```shell
tox devenv -e integration
source venv/bin/activate
```

### Testing

```shell
tox run -e format        # update your code according to linting rules
tox run -e lint          # code style
tox run -e unit          # unit tests
charmcraft test lxd-vm:  # integration tests
tox                      # runs 'lint' and 'unit' environments
```

## Build charm

The build environment assumes that there are preinstalled on the system:

* [tox](https://tox.wiki/) (version 4+ !!!)
* [poetry](https://python-poetry.org/)
* [charmcraft](https://snapcraft.io/charmcraft)
* [charmcraftcache](https://github.com/canonical/charmcraftcache)
* [pipx](https://pipx.pypa.io/stable/installation/)
* [libpq-dev](https://www.postgresql.org/docs/current/libpq.html)

To build the charm it is also necessary at least 5GB of free disk space and
it is recommended to provide 4+ CPU cores and 8GB+ RAM for a decent build speed.

To install all above build dependencies (assuming you are on Ubuntu 22.04 LTS):

```shell
sudo snap install charmcraft --classic

sudo snap install lxd # should be pre-installed on 22.04
lxd init --auto       # init LXD (if never used earlier)

sudo apt update && sudo apt install --yes libpq-dev pipx

sudo apt purge tox # if old tox version is installed from apt

pipx ensurepath
pipx install tox
pipx install poetry
pipx install charmcraftcache
```

Ensure local pip binaries are in your $PATH (otherwise re-login to your shell):

```shell
charmcraftcache --help
```

Build the charm (inside this Git repository):

```shell
charmcraftcache pack
```

### Deploy

```bash
# Create a model
juju add-model dev

# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Deploy the charm
juju deploy ./postgresql_ubuntu-22.04-amd64.charm
```

## Canonical Contributor Agreement

Canonical welcomes contributions to the PostgreSQL Operator. Please check out our
[contributor agreement](https://ubuntu.com/legal/contributors)if you're
interested in contributing to the solution.
