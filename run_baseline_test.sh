#!/bin/bash
set -e
export PATH=$HOME/.local/bin:$PATH
LOG=$HOME/baseline_test.log
exec > $LOG 2>&1

echo "=== BASELINE TEST (UNFIXED CHARM) ==="
echo "Started at $(date)"

cd ~/postgresql-operator

# Step 1: Build unfixed charm
echo ""
echo "=== Step 1: Building unfixed charm ==="

# Revert charm.py and cluster.py to remove the fix
python3 - << 'PYEOF'
# Revert cluster.py
with open("src/cluster.py") as f:
    content = f.read()

# render_file: change return type back to None
content = content.replace(
    "def render_file(self, path: str, content: str, mode: int, change_owner: bool = True) -> bool:",
    "def render_file(self, path: str, content: str, mode: int, change_owner: bool = True) -> None:"
)

# Remove the Returns docstring from render_file
content = content.replace(
    """
        Returns:
            Whether the file content was changed.
        \"\"\"""",
    """
        \"\"\""""
)

# Remove content comparison block
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

# Remove "return True" after chown
content = content.replace(
    """            self._change_owner(path)
        return True

    def render_patroni_yml_file(""",
    """            self._change_owner(path)

    def render_patroni_yml_file("""
)

# render_patroni_yml_file: change return type back to None
content = content.replace(
    ") -> bool:\n"
    '        """Render the Patroni configuration file.',
    ") -> None:\n"
    '        """Render the Patroni configuration file.'
)

# Remove Returns docstring from render_patroni_yml_file
content = content.replace(
    """
        Returns:
            Whether the configuration file content was changed.
        \"\"\"""",
    """
        \"\"\""""
)

# Change "return self.render_file" back to "self.render_file"
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

rm -f *.charm
charmcraft pack 2>&1 | tail -3

# Step 2: Deploy
echo ""
echo "=== Step 2: Deploying unfixed charm ==="
juju add-model baseline-test 2>&1
juju switch lxd:baseline-test
juju deploy ~/postgresql-operator/postgresql_ubuntu@22.04-amd64.charm postgresql -n 3 2>&1
juju deploy postgresql-test-app 2>&1
juju integrate postgresql:database postgresql-test-app:database 2>&1

echo "Waiting for deployment..."
for i in $(seq 1 60); do
  all_idle=$(juju status 2>&1 | grep -c "active.*idle" || true)
  echo "$(date +%H:%M:%S) idle count: $all_idle"
  if [ "$all_idle" -ge "4" ]; then
    echo "All ready!"
    break
  fi
  sleep 15
done

juju status 2>&1

# Step 3: Verify unfixed code
echo ""
echo "=== Step 3: Verify unfixed code deployed ==="
juju ssh postgresql/1 'sudo grep "def render_file" /var/lib/juju/agents/unit-postgresql-1/charm/src/cluster.py' 2>&1
juju ssh postgresql/1 'sudo grep "def _handle_postgresql_restart_need" /var/lib/juju/agents/unit-postgresql-1/charm/src/charm.py' 2>&1

# Step 4: Clear logs and capture baseline
echo ""
echo "=== Step 4: Clear logs ==="
for unit in postgresql/0 postgresql/1 postgresql/2; do
  juju ssh "$unit" 'sudo bash -c "for f in /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log; do truncate -s 0 \$f; done"' 2>&1
done

echo ""
echo "=== Baseline hashes ==="
for unit in postgresql/0 postgresql/1 postgresql/2; do
  hash=$(juju ssh "$unit" 'sudo md5sum /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml' 2>&1)
  echo "$unit: $hash"
done

echo ""
echo "=== Patroni cluster ==="
juju ssh postgresql/0 'sudo -H -u snap_daemon charmed-postgresql.patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list' 2>&1

# Switchover 1: 0->1
echo ""
echo "=== TEST: Switchover 0->1 ==="
juju ssh postgresql/0 'sudo -H -u snap_daemon charmed-postgresql.patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml switchover --leader postgresql-0 --candidate postgresql-1 --force' 2>&1

sleep 60

# Switchover 2: 1->0
echo ""
echo "=== TEST: Switchover 1->0 ==="
juju ssh postgresql/1 'sudo -H -u snap_daemon charmed-postgresql.patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml switchover --leader postgresql-1 --candidate postgresql-0 --force' 2>&1

sleep 30

# 10 rapid hooks
echo ""
echo "=== TEST: 10 rapid update-status hooks on postgresql/1 ==="
for i in $(seq 1 10); do
  juju exec --unit postgresql/1 'JUJU_DISPATCH_PATH=hooks/update-status ./dispatch' 2>&1 &
done
wait
echo "All dispatched"

sleep 30

# Step 5: Collect results
echo ""
echo "========================================"
echo "=== RESULTS: UNFIXED CHARM BASELINE ==="
echo "========================================"

echo ""
echo "=== SIGHUPs per unit ==="
for unit in postgresql/0 postgresql/1 postgresql/2; do
  sighups=$(juju ssh "$unit" 'sudo bash -c "total=0; for f in /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log; do c=\$(grep -c \"received SIGHUP\" \$f 2>/dev/null || echo 0); total=\$((total + c)); done; echo \$total"' 2>&1)
  echo "$unit: $sighups SIGHUPs"
done

echo ""
echo "=== SIGHUP log entries on postgresql/1 ==="
juju ssh postgresql/1 'sudo bash -c "grep \"received SIGHUP\" /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql-*.log 2>/dev/null || echo none"' 2>&1

echo ""
echo "=== Hooks on postgresql/1 (last 30) ==="
juju ssh postgresql/1 'sudo bash -c "grep \"ran.*hook\" /var/log/juju/unit-postgresql-1.log | tail -n 30"' 2>&1

echo ""
echo "=== Patroni yaml hashes after ==="
for unit in postgresql/0 postgresql/1 postgresql/2; do
  hash=$(juju ssh "$unit" 'sudo md5sum /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml' 2>&1)
  echo "$unit: $hash"
done

echo ""
echo "=== Cluster state ==="
juju ssh postgresql/0 'sudo -H -u snap_daemon charmed-postgresql.patronictl -c /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml list' 2>&1

echo ""
echo "=== Juju status ==="
juju status 2>&1

# Restore fixed code
echo ""
echo "=== Restoring fixed code ==="
cd ~/postgresql-operator
cp src/charm.py.fixed src/charm.py
cp src/cluster.py.fixed src/cluster.py

echo ""
echo "=== DONE at $(date) ==="
