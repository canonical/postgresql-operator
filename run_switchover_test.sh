#!/bin/bash
set -e
export PATH=$HOME/.local/bin:$PATH
LOG=$HOME/switchover_test.log
exec > $LOG 2>&1

PATRONI_CMD='sudo -H -u snap_daemon charmed-postgresql.patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml'

echo "=== 10-SWITCHOVER COMPARISON TEST ==="
echo "Started at $(date)"

cd ~/postgresql-operator

CHARM_FILE=~/postgresql-operator/postgresql_ubuntu@22.04-amd64.charm

wait_for_ready() {
  echo "Waiting for deployment to be ready..."
  for i in $(seq 1 80); do
    all_idle=$(juju status 2>&1 | grep -c "active.*idle" || true)
    echo "$(date +%H:%M:%S) idle count: $all_idle"
    if [ "$all_idle" -ge "4" ]; then
      echo "All units ready!"
      return 0
    fi
    sleep 15
  done
  echo "WARNING: Timed out waiting for ready"
  juju status 2>&1
  return 1
}

wait_for_cluster() {
  echo "Waiting for Patroni cluster to stabilize..."
  sleep 15
  for i in $(seq 1 20); do
    # Count both "running" (leader) and "streaming" (replicas) members
    members=$(juju ssh postgresql/0 "$PATRONI_CMD list 2>/dev/null" 2>&1 | grep -cE "running|streaming" || true)
    echo "$(date +%H:%M:%S) active members: $members"
    if [ "$members" -ge "3" ]; then
      echo "Cluster stable"
      return 0
    fi
    sleep 10
  done
  echo "WARNING: Cluster not fully stable"
}

clear_pg_logs() {
  echo "Clearing PostgreSQL logs..."
  for unit in postgresql/0 postgresql/1 postgresql/2; do
    juju ssh "$unit" 'sudo bash -c "for f in /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log; do truncate -s 0 \$f 2>/dev/null; done"' 2>&1
  done
}

get_leader() {
  # patronictl list output: | postgresql-0 | 10.x.x.x | Leader | running | ...
  # Field $2 is the member name
  juju ssh postgresql/0 "$PATRONI_CMD list 2>/dev/null" 2>&1 | grep "Leader" | awk '{print $2}'
}

count_sighups() {
  local label=$1
  echo ""
  echo "=== SIGHUPs per unit ($label) ==="
  for unit in postgresql/0 postgresql/1 postgresql/2; do
    sighups=$(juju ssh "$unit" 'sudo bash -c "total=0; for f in /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log; do c=$(grep -c \"received SIGHUP\" $f 2>/dev/null || echo 0); total=$((total + c)); done; echo $total"' 2>&1)
    echo "$unit: $sighups SIGHUPs"
  done
}

run_10_switchovers() {
  echo ""
  echo "=== Running 10 switchovers ==="
  for i in $(seq 1 10); do
    leader=$(get_leader)
    if [ "$leader" = "postgresql-0" ]; then
      candidate="postgresql-1"
    else
      candidate="postgresql-0"
    fi
    echo ""
    echo "--- Switchover $i: $leader -> $candidate ---"
    juju ssh postgresql/0 "$PATRONI_CMD switchover --leader $leader --candidate $candidate --force" 2>&1

    # Wait for switchover to complete
    sleep 20
    for j in $(seq 1 10); do
      new_leader=$(get_leader)
      if [ "$new_leader" = "$candidate" ]; then
        echo "Switchover $i complete: leader is now $new_leader"
        break
      fi
      echo "  waiting... current leader: $new_leader"
      sleep 5
    done
  done

  # Let hooks settle
  echo ""
  echo "Waiting 60s for all hooks to settle..."
  sleep 60
}

deploy_and_test() {
  local model_name=$1
  local label=$2

  echo ""
  echo "========================================"
  echo "=== TESTING: $label ==="
  echo "========================================"

  juju switch lxd 2>&1
  juju add-model "$model_name" 2>&1
  juju switch "lxd:$model_name" 2>&1
  juju deploy "$CHARM_FILE" postgresql -n 3 2>&1
  juju deploy postgresql-test-app 2>&1
  juju integrate postgresql:database postgresql-test-app:database 2>&1

  wait_for_ready
  wait_for_cluster

  echo ""
  echo "=== Patroni cluster state ==="
  juju ssh postgresql/0 "$PATRONI_CMD list" 2>&1

  echo ""
  echo "=== Baseline hashes ==="
  for unit in postgresql/0 postgresql/1 postgresql/2; do
    hash=$(juju ssh "$unit" 'sudo md5sum /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml' 2>&1)
    echo "$unit: $hash"
  done

  clear_pg_logs

  run_10_switchovers

  echo ""
  echo "========================================"
  echo "=== RESULTS: $label ==="
  echo "========================================"

  count_sighups "$label"

  echo ""
  echo "=== Patroni yaml hashes after ==="
  for unit in postgresql/0 postgresql/1 postgresql/2; do
    hash=$(juju ssh "$unit" 'sudo md5sum /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml' 2>&1)
    echo "$unit: $hash"
  done

  echo ""
  echo "=== Cluster state after ==="
  juju ssh postgresql/0 "$PATRONI_CMD list" 2>&1

  echo ""
  echo "=== Juju status ==="
  juju status 2>&1
}

########################################
# TEST 1: FIXED CHARM
########################################

# Reuse existing sw-fixed model if it exists, otherwise build & deploy
juju switch lxd 2>&1
existing=$(juju models 2>&1 | grep -c "sw-fixed" || true)
if [ "$existing" -gt "0" ]; then
  echo ""
  echo "=== Reusing existing sw-fixed model ==="
  juju switch "lxd:sw-fixed" 2>&1

  wait_for_cluster

  echo ""
  echo "=== Patroni cluster state ==="
  juju ssh postgresql/0 "$PATRONI_CMD list" 2>&1

  echo ""
  echo "=== Baseline hashes ==="
  for unit in postgresql/0 postgresql/1 postgresql/2; do
    hash=$(juju ssh "$unit" 'sudo md5sum /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml' 2>&1)
    echo "$unit: $hash"
  done

  clear_pg_logs
  run_10_switchovers

  echo ""
  echo "========================================"
  echo "=== RESULTS: FIXED CHARM (10 switchovers) ==="
  echo "========================================"

  count_sighups "FIXED CHARM"

  echo ""
  echo "=== Patroni yaml hashes after ==="
  for unit in postgresql/0 postgresql/1 postgresql/2; do
    hash=$(juju ssh "$unit" 'sudo md5sum /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml' 2>&1)
    echo "$unit: $hash"
  done

  echo ""
  echo "=== Cluster state after ==="
  juju ssh postgresql/0 "$PATRONI_CMD list" 2>&1

  echo ""
  echo "=== Juju status ==="
  juju status 2>&1
else
  echo ""
  echo "=== Building FIXED charm ==="
  rm -f *.charm
  charmcraft pack 2>&1 | tail -5
  echo "Fixed charm built."

  deploy_and_test "sw-fixed" "FIXED CHARM (10 switchovers)"
fi

# Destroy fixed model
echo ""
echo "=== Destroying sw-fixed model ==="
juju destroy-model sw-fixed --destroy-storage --no-prompt 2>&1

########################################
# TEST 2: UNFIXED CHARM
########################################
echo ""
echo "=== Reverting to UNFIXED code ==="
cd ~/postgresql-operator

python3 - << 'PYEOF'
# Revert cluster.py
with open("src/cluster.py") as f:
    content = f.read()

content = content.replace(
    "def render_file(self, path: str, content: str, mode: int, change_owner: bool = True) -> bool:",
    "def render_file(self, path: str, content: str, mode: int, change_owner: bool = True) -> None:"
)

content = content.replace(
    """
        Returns:
            Whether the file content was changed.
        \"\"\"""",
    """
        \"\"\""""
)

content = content.replace(
    """        # Skip writing if the content is identical to avoid unnecessary Patroni reloads.
        try:
            with open(path) as file:
                if file.read() == content:
                    logger.debug("File %s content unchanged, skipping write", path)
                    return False
        except FileNotFoundError:
            pass
        # Write the content to the file.""",
    "        # Write the content to the file."
)

content = content.replace(
    """            self._change_owner(path)
        return True

    def render_patroni_yml_file(""",
    """            self._change_owner(path)

    def render_patroni_yml_file("""
)

content = content.replace(
    ") -> bool:\n"
    '        """Render the Patroni configuration file.',
    ") -> None:\n"
    '        """Render the Patroni configuration file.'
)

content = content.replace(
    """
        Returns:
            Whether the configuration file content was changed.
        \"\"\"""",
    """
        \"\"\""""
)

content = content.replace(
    '        return self.render_file(f"{PATRONI_CONF_PATH}/patroni.yaml", rendered, 0o600)',
    '        self.render_file(f"{PATRONI_CONF_PATH}/patroni.yaml", rendered, 0o600)'
)

with open("src/cluster.py", "w") as f:
    f.write(content)

# Revert charm.py
with open("src/charm.py") as f:
    content = f.read()

content = content.replace(
    "        patroni_config_changed = self._patroni.render_patroni_yml_file(",
    "        self._patroni.render_patroni_yml_file("
)

content = content.replace(
    """        if not self._patroni.member_started:
            # Potentially expired cert reloading and deferring
            if patroni_config_changed:
                self._patroni.reload_patroni_configuration()""",
    """        if not self._patroni.member_started:
            # Potentially expired cert reloading and deferring
            self._patroni.reload_patroni_configuration()"""
)

content = content.replace(
    """        self._handle_postgresql_restart_need(
            self.unit_peer_data.get("config_hash") != self.generate_config_hash,
            patroni_config_changed=patroni_config_changed,
        )""",
    """        self._handle_postgresql_restart_need(
            self.unit_peer_data.get("config_hash") != self.generate_config_hash
        )"""
)

content = content.replace(
    """    def _handle_postgresql_restart_need(
        self, config_changed: bool, patroni_config_changed: bool = True
    ) -> None:
        \"\"\"Handle PostgreSQL restart need based on the TLS configuration and configuration changes.\"\"\"
        restart_postgresql = self.is_tls_enabled != self.postgresql.is_tls_enabled()
        if patroni_config_changed or restart_postgresql:
            try:
                self._patroni.reload_patroni_configuration()
            except Exception as e:
                logger.error(f"Reload patroni call failed! error: {e!s}")
        else:
            logger.debug("Skipping Patroni reload: configuration file unchanged")
        self.unit_peer_data.update({"tls": "enabled" if self.is_tls_enabled else ""})""",
    """    def _handle_postgresql_restart_need(self, config_changed: bool) -> None:
        \"\"\"Handle PostgreSQL restart need based on the TLS configuration and configuration changes.\"\"\"
        restart_postgresql = self.is_tls_enabled != self.postgresql.is_tls_enabled()
        try:
            self._patroni.reload_patroni_configuration()
            self.unit_peer_data.update({"tls": "enabled" if self.is_tls_enabled else ""})
        except Exception as e:
            logger.error(f"Reload patroni call failed! error: {e!s}")"""
)

with open("src/charm.py", "w") as f:
    f.write(content)
PYEOF

echo "Verifying revert:"
grep "def render_file" src/cluster.py
grep "def _handle_postgresql_restart_need" src/charm.py

echo ""
echo "=== Building UNFIXED charm ==="
rm -f *.charm
charmcraft pack 2>&1 | tail -5
echo "Unfixed charm built."

deploy_and_test "sw-unfixed" "UNFIXED CHARM (10 switchovers)"

########################################
# RESTORE FIXED CODE
########################################
echo ""
echo "=== Restoring fixed code ==="
cd ~/postgresql-operator
cp src/charm.py.fixed src/charm.py
cp src/cluster.py.fixed src/cluster.py
echo "Fixed code restored."

echo ""
echo "========================================"
echo "=== TEST COMPLETE at $(date) ==="
echo "========================================"
