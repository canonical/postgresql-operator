# Ext4 Storage Volume Setup for Charmed PostgreSQL Testing

## Overview

This guide documents how to set up real ext4-formatted storage volumes for
testing `lost+found` cleanup behaviour in the Charmed PostgreSQL operator.
The key insight is that `mkfs.ext4` creates a `lost+found` directory at the
root of every new filesystem, and the charm must remove these before
PostgreSQL starts.

### Why LXD VMs, not LXD containers

| Approach | Problem |
|---|---|
| `loop` storage pool inside an LXD **container** | Loop devices are not kernel-namespaced; the host's 50+ snap mounts exhaust `/dev/loop*` before the container can use any |
| LXD `lvm` storage driver via Juju | Juju's `lxd` storage provider rejects LVM-backed pools (`driver=lvm` is unsupported) |
| LXD **VMs** (`virt-type=virtual-machine`) | Full kernel isolation; loop devices have their own namespace; `loop` Juju storage pool works perfectly |

**Requirement: the host must expose hardware virtualisation (`vmx`/`svm` CPU flags).**

```bash
egrep -c "(vmx|svm)" /proc/cpuinfo   # must return > 0
```

---

## Prerequisites

- Ubuntu 24.04 bare metal (or a VM with nested virtualisation enabled)
- `lxd` snap installed and initialised
- `juju` 3.x installed
- `charmcraft` installed (for building the charm)

---

## Step-by-step setup

### 1. Initialise LXD

```bash
sudo lxd init --auto
sudo usermod -aG lxd $USER
newgrp lxd
```

### 2. Install charmcraft

```bash
sudo snap install charmcraft --classic
```

### 3. Bootstrap a Juju LXD controller

```bash
juju bootstrap localhost lxd-controller
```

### 4. Add a model

```bash
juju add-model lf-test
```

### 5. Create the `lxd-ext4` Juju storage pool

Use the `loop` provider. Inside an LXD VM each loop-backed volume gets its
own `mkfs.ext4` run, which creates a real ext4 filesystem complete with
`lost+found`.

```bash
juju create-storage-pool lxd-ext4 loop
```

Verify:

```bash
juju storage-pools
# lxd-ext4   loop
```

### 6. Build the charm

```bash
cd postgresql-operator
charmcraft pack
```

### 7. Deploy with LXD VM constraints and `lxd-ext4` for all four storages

The four storage mounts defined in `metadata.yaml` are `archive`, `data`,
`logs`, and `temp`. All must be backed by `lxd-ext4` so they each get a
dedicated ext4 filesystem.

The `root-disk` constraint must be large enough to hold all four loop backing
files plus the OS and Juju agent. The four storages total 16 GiB; 50 GiB
leaves comfortable headroom.

```bash
juju deploy ./postgresql_ubuntu@24.04-amd64.charm \
  --constraints "virt-type=virtual-machine root-disk=50G" \
  --storage archive=lxd-ext4,2G \
  --storage data=lxd-ext4,10G \
  --storage logs=lxd-ext4,2G \
  --storage temp=lxd-ext4,2G
```

### 8. Wait for the install hook to run

```bash
juju status --watch 5s
```

Once the unit reaches `(install)` the storage volumes are already mounted.

### 9. Verify `lost+found` was created and then removed by the charm

Check the debug log for the charm's removal messages:

```bash
juju debug-log --no-tail --level DEBUG | grep -i "lost"
```

Expected output (one line per storage, all at the same timestamp during the
`install` hook):

```
unit-postgresql-0: INFO ... Removing /var/snap/charmed-postgresql/common/data/archive/lost+found
unit-postgresql-0: INFO ... Removing /var/snap/charmed-postgresql/common/var/lib/postgresql/lost+found
unit-postgresql-0: INFO ... Removing /var/snap/charmed-postgresql/common/data/logs/lost+found
unit-postgresql-0: INFO ... Removing /var/snap/charmed-postgresql/common/data/temp/lost+found
```

Confirm the directories are gone:

```bash
juju exec --unit postgresql/0 -- \
  ls /var/snap/charmed-postgresql/common/data/archive/lost+found \
     /var/snap/charmed-postgresql/common/var/lib/postgresql/lost+found \
     /var/snap/charmed-postgresql/common/data/logs/lost+found \
     /var/snap/charmed-postgresql/common/data/temp/lost+found
# Expected: ls: cannot access '...': No such file or directory  (×4)
```

### 10. Confirm the charm reaches active/idle

```bash
juju status
# postgresql/0*  active  idle  ...  5432/tcp  Primary
```

---

## Storage paths reference

These are the mount points the charm and this guide refer to, defined in
`src/constants.py` and `metadata.yaml`:

| Storage name | Mount path inside unit |
|---|---|
| `archive` | `/var/snap/charmed-postgresql/common/data/archive` |
| `data` | `/var/snap/charmed-postgresql/common/var/lib/postgresql` |
| `logs` | `/var/snap/charmed-postgresql/common/data/logs` |
| `temp` | `/var/snap/charmed-postgresql/common/data/temp` |

---

## Charm code reference

The cleanup logic lives in `src/charm.py`:

```python
def _remove_lost_and_found(self) -> None:
    """Remove the lost+found directory from the root of each storage if it exists."""
    for storage_path in STORAGE_PATHS:
        lost_and_found_path = Path(storage_path) / "lost+found"
        if lost_and_found_path.is_dir():
            logger.info(f"Removing {lost_and_found_path}")
            try:
                shutil.rmtree(lost_and_found_path)
            except OSError:
                logger.exception(f"Failed to remove {lost_and_found_path}")
```

It is called at the top of both `_on_install` and `_on_start` so that
`lost+found` is removed regardless of which hook fires first after storage
is attached.