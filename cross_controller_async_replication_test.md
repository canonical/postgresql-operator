# Cross-Controller Async Replication Test (Separate Subnets)

This document describes the full procedure for setting up and validating
cross-controller async replication between two PostgreSQL clusters managed by
separate Juju controllers, each on its own LXD network/subnet.

## Prerequisites

- A Linux host with LXD installed and initialised
- Juju 3.6+ CLI installed
- An existing Juju controller bootstrapped on LXD (referred to as `lxd` below,
  using the default `lxdbr1` bridge on e.g. `10.42.197.0/24`)
- The PostgreSQL charm built locally (e.g. `postgresql_ubuntu@22.04-amd64.charm`)

## 1. Create a second LXD network

Create a separate bridge so the second controller's machines live on a
different subnet from the first controller's machines.

```bash
lxc network create lxdbr2 \
  ipv4.address=10.43.0.1/24 \
  ipv4.nat=true \
  ipv6.address=none
```

Verify:

```bash
lxc network show lxdbr2
# Should show ipv4.address: 10.43.0.1/24
```

## 2. Disable NAT between the two bridge subnets

By default, LXD masquerades (NATs) traffic leaving each bridge to non-local
destinations. This means a container on `lxdbr2` (10.43.0.x) connecting to a
container on `lxdbr1` (10.42.197.x) will appear with the host's bridge IP
(10.42.197.1) instead of its own IP. PostgreSQL's pg_hba `/32` rules need to
see the real container IP.

Insert nftables accept rules **before** the masquerade rules in both bridge
chains:

```bash
sudo nft insert rule inet lxd pstrt.lxdbr2 \
  ip saddr 10.43.0.0/24 ip daddr 10.42.197.0/24 accept

sudo nft insert rule inet lxd pstrt.lxdbr1 \
  ip saddr 10.42.197.0/24 ip daddr 10.43.0.0/24 accept
```

Verify:

```bash
sudo nft list chain inet lxd pstrt.lxdbr2
# First rule should be: ip saddr 10.43.0.0/24 ip daddr 10.42.197.0/24 accept
# Followed by:          ip saddr 10.43.0.0/24 ip daddr != 10.43.0.0/24 ... masquerade

sudo nft list chain inet lxd pstrt.lxdbr1
# First rule should be: ip saddr 10.42.197.0/24 ip daddr 10.43.0.0/24 accept
# Followed by:          ip saddr 10.42.197.0/24 ip daddr != 10.42.197.0/24 ... masquerade
```

> **Note**: These nftables rules do not persist across LXD restarts. LXD
> regenerates firewall rules when it starts. For a permanent solution, create a
> systemd service or use LXD's raw.dnsmasq/firewall config.

> **Note**: If `lxdbr2` is created _after_ the first controller is already
> running, the nft rules for `lxdbr2` won't exist until a container uses it.
> Run the `nft insert` commands after bootstrapping the second controller
> (step 5), when both chains exist.

## 3. Create a LXD project for the second controller

Using a dedicated LXD project lets us assign a custom default profile that
wires `eth0` to `lxdbr2`, without affecting the first controller's machines.

```bash
lxc project create juju-remote
```

Configure the default profile inside the project to use `lxdbr2`:

```bash
lxc profile device add default eth0 nic \
  nictype=bridged parent=lxdbr2 --project juju-remote

lxc profile device add default root disk \
  pool=default path=/ --project juju-remote
```

Verify:

```bash
lxc profile show default --project juju-remote
# eth0 should reference lxdbr2
# root should reference the default storage pool
```

## 4. Register the LXD project as a Juju cloud

Create a cloud definition that targets the new project:

```bash
cat > /tmp/lxd-remote-cloud.yaml <<'EOF'
clouds:
  lxd-remote:
    type: lxd
    auth-types: [certificate]
    regions:
      localhost: {}
    config:
      project: juju-remote
EOF

juju add-cloud lxd-remote /tmp/lxd-remote-cloud.yaml --client
```

Add credentials using the existing LXD client certificate:

```bash
CLIENT_CERT=$(cat ~/.local/share/juju/lxd/client.crt)
CLIENT_KEY=$(cat ~/.local/share/juju/lxd/client.key)

cat > /tmp/lxd-remote-creds.yaml <<EOF
credentials:
  lxd-remote:
    lxd-remote-cred:
      auth-type: certificate
      client-cert: |
$(echo "$CLIENT_CERT" | sed 's/^/        /')
      client-key: |
$(echo "$CLIENT_KEY" | sed 's/^/        /')
EOF

juju add-credential lxd-remote --client -f /tmp/lxd-remote-creds.yaml
```

## 5. Bootstrap the second controller

```bash
juju bootstrap lxd-remote lxd2
```

Verify the controller machine got an IP on the `10.43.0.0/24` subnet:

```bash
juju show-controller lxd2 --format json | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['lxd2']['details']['api-endpoints'])"
# Should show 10.43.x.x:17070
```

If you haven't yet added the nft NAT bypass rules from step 2, do it now
(the `pstrt.lxdbr2` chain is created when the first container starts on
`lxdbr2`).

## 6. Deploy PostgreSQL on both controllers

### Primary cluster (controller: lxd, subnet: 10.42.197.0/24)

```bash
juju switch lxd
juju add-model primary-dc
juju deploy ./postgresql_ubuntu@22.04-amd64.charm postgresql \
  -n 3 --config profile=testing
```

### Standby cluster (controller: lxd2, subnet: 10.43.0.0/24)

```bash
juju switch lxd2
juju add-model remote-dc
juju deploy ./postgresql_ubuntu@22.04-amd64.charm postgresql \
  -n 3 --config profile=testing
```

### Wait for both clusters to become active

```bash
juju status -m lxd:primary-dc --watch 10s
# Wait until all units show active/idle, IPs on 10.42.197.x

juju status -m lxd2:remote-dc --watch 10s
# Wait until all units show active/idle, IPs on 10.43.0.x
```

## 7. Set up cross-controller async replication

### Offer the replication endpoint on the primary cluster

```bash
juju switch lxd:primary-dc
juju offer postgresql:replication-offer replication-offer
```

### Consume the offer on the standby cluster and integrate

```bash
juju switch lxd2:remote-dc
juju consume lxd:admin/primary-dc.replication-offer
juju integrate replication-offer postgresql:replication
```

### Wait for the relation to settle

The cross-controller relation may take a moment to propagate all unit data.
Wait ~30 seconds before running `create-replication`.

### Create the replication

```bash
juju run -m lxd:primary-dc postgresql/leader create-replication --wait=10m
```

> **Note**: If `create-replication` fails with `StopIteration`, the relation
> data hasn't fully propagated yet. Wait 30 seconds and retry.

### Wait for the standby cluster to settle

Enable fast update-status to speed up convergence:

```bash
juju model-config -m lxd:primary-dc update-status-hook-interval=10s
juju model-config -m lxd2:remote-dc update-status-hook-interval=10s
```

Wait until both clusters show correct roles:

```bash
juju status -m lxd:primary-dc
# App message: "Primary"

juju status -m lxd2:remote-dc
# App message: "Standby", all units active/idle
```

Reset the interval after settling:

```bash
juju model-config -m lxd:primary-dc update-status-hook-interval=5m
juju model-config -m lxd2:remote-dc update-status-hook-interval=5m
```

## 8. Validate the setup

### 8.1 Verify pg_hba rules contain cross-subnet IPs

On the **primary cluster leader**, pg_hba should contain:
- `127.0.0.1/32` — localhost
- `self_ip/32` — the unit's own IP (e.g. `10.42.197.179/32`)
- `/32` entries for each remote standby unit IP from `extra_replication_endpoints`
  (e.g. `10.43.0.164/32`, `10.43.0.91/32`, `10.43.0.87/32`)
- `/32` entries for local peer IPs (e.g. `10.42.197.211/32`, `10.42.197.83/32`)

```bash
juju ssh -m lxd:primary-dc postgresql/0 \
  "sudo cat /var/snap/charmed-postgresql/common/var/lib/postgresql/pg_hba.conf" \
  | grep replication
```

Example output:

```
host replication replication 127.0.0.1/32 md5
host replication replication 10.42.197.179/32 md5          # self_ip
host replication replication 10.43.0.164/32 md5            # remote standby (lxdbr2)
host replication replication 10.43.0.91/32 md5             # remote standby (lxdbr2)
host replication replication 10.43.0.87/32 md5             # remote standby (lxdbr2)
host     replication    replication    10.42.197.211/32    md5   # local peer
host     replication    replication    10.42.197.83/32     md5   # local peer
```

### 8.2 Verify Patroni cluster state

```bash
# Primary cluster
juju exec -m lxd:primary-dc --unit postgresql/0 \
  "sudo -u snap_daemon charmed-postgresql.patronictl \
   -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list"

# Standby cluster
juju exec -m lxd2:remote-dc --unit postgresql/0 \
  "sudo -u snap_daemon charmed-postgresql.patronictl \
   -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list"
```

Expected:
- Primary cluster: one Leader (10.42.x.x), two Sync Standby, all `streaming`
- Standby cluster: one Standby Leader (10.43.x.x), two Replica, all `streaming`
- Same cluster system ID on both clusters
- Same timeline on all members

### 8.3 Verify cross-cluster replication is streaming

Get the operator password:

```bash
juju run -m lxd:primary-dc postgresql/leader get-password --wait=30s
```

Find which primary node the standby leader connects to:

```bash
juju exec -m lxd2:remote-dc --unit postgresql/0 \
  "sudo -u snap_daemon cat /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml \
   | grep -A 5 standby_cluster"
# Note the 'host' field
```

Check that node's replication (replace `<unit>` and `<password>`):

```bash
juju exec -m lxd:primary-dc --unit <unit> \
  "sudo -u snap_daemon PGPASSWORD=<password> \
   charmed-postgresql.psql -h /tmp -U operator -d postgres \
   -c \"SELECT client_addr, state, application_name FROM pg_stat_replication;\""
```

Expected: the standby leader's `10.43.x.x` IP appears with `state = streaming`.

### 8.4 Verify data replication across subnets

```bash
# Write on primary (10.42.x.x)
juju exec -m lxd:primary-dc --unit postgresql/0 \
  "sudo -u snap_daemon PGPASSWORD=<password> \
   charmed-postgresql.psql -h /tmp -U operator -d postgres \
   -c \"CREATE TABLE cross_subnet_test (id serial PRIMARY KEY, data text, subnet text);
        INSERT INTO cross_subnet_test (data, subnet) VALUES ('row1', '10.42.x.x');\""

# Read on standby (10.43.x.x)
juju exec -m lxd2:remote-dc --unit postgresql/0 \
  "sudo -u snap_daemon PGPASSWORD=<password> \
   charmed-postgresql.psql -h /tmp -U operator -d postgres \
   -c \"SELECT * FROM cross_subnet_test;\""
```

Expected: the row appears on the standby.

## 9. Test switchover across subnets

### 9.1 Promote standby (10.43.x.x) to primary

```bash
juju run -m lxd2:remote-dc postgresql/leader \
  promote-to-primary scope=cluster --wait=10m
```

Wait for roles to flip:

```bash
juju status -m lxd:primary-dc    # Should show "Standby"
juju status -m lxd2:remote-dc    # Should show "Primary"
```

### 9.2 Verify pg_hba updated on new primary

```bash
juju ssh -m lxd2:remote-dc postgresql/0 \
  "sudo cat /var/snap/charmed-postgresql/common/var/lib/postgresql/pg_hba.conf" \
  | grep replication
```

Expected: the new primary's pg_hba now contains the old primary cluster's
`10.42.x.x` IPs in `extra_replication_endpoints` (all with `/32`).

### 9.3 Write data on new primary and verify replication

```bash
# Write on new primary (10.43.x.x)
juju exec -m lxd2:remote-dc --unit postgresql/0 \
  "sudo -u snap_daemon PGPASSWORD=<password> \
   charmed-postgresql.psql -h /tmp -U operator -d postgres \
   -c \"INSERT INTO cross_subnet_test (data, subnet) VALUES ('row2', '10.43.x.x');\""

# Read on new standby (10.42.x.x)
juju exec -m lxd:primary-dc --unit postgresql/0 \
  "sudo -u snap_daemon PGPASSWORD=<password> \
   charmed-postgresql.psql -h /tmp -U operator -d postgres \
   -c \"SELECT * FROM cross_subnet_test;\""
```

Expected: both rows visible on the new standby.

### 9.4 Switch back to original primary

```bash
juju run -m lxd:primary-dc postgresql/leader \
  promote-to-primary scope=cluster --wait=10m
```

Verify:
- primary-dc shows "Primary" again
- remote-dc shows "Standby" again
- All data preserved (both rows visible)
- Timeline advanced (check Patroni `TL` column — should be 3 after two switchovers)

## 10. Cleanup

```bash
# Reset update-status interval
juju model-config -m lxd:primary-dc update-status-hook-interval=5m
juju model-config -m lxd2:remote-dc update-status-hook-interval=5m

# Destroy models (--force to skip relation removal)
juju destroy-model lxd:primary-dc --destroy-storage --no-prompt --force
juju destroy-model lxd2:remote-dc --destroy-storage --no-prompt --force

# Destroy second controller
juju destroy-controller lxd2 --destroy-all-models --destroy-storage --no-prompt

# Remove Juju cloud and credentials
juju remove-cloud lxd-remote --client
juju remove-credential lxd-remote lxd-remote-cred --client

# Remove nft rules (optional — LXD regenerates them on restart)
sudo nft delete rule inet lxd pstrt.lxdbr1 handle $(sudo nft -a list chain inet lxd pstrt.lxdbr1 | grep "10.43.0.0" | awk '{print $NF}')
sudo nft delete rule inet lxd pstrt.lxdbr2 handle $(sudo nft -a list chain inet lxd pstrt.lxdbr2 | grep "10.42.197.0" | awk '{print $NF}')

# Remove LXD resources
lxc project delete juju-remote
lxc network delete lxdbr2
```
