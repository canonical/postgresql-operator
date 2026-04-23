## Issue

Every hook invocation that reaches `update_config()` unconditionally rewrites `patroni.yaml` and sends a SIGHUP to Patroni, even when the file content hasn't changed. Under normal operation this causes minor unnecessary reloads, but during hook storms (e.g. a flood of `secret-changed` events from a Juju secret revision inconsistency), this causes `primary_conninfo` in `postgresql.conf` to flap as Patroni re-evaluates replication topology on each reload, destabilizing replicas.

This was observed in a production incident where, after resolving a Juju-side secret issue, the backlogged hooks drained all at once, each one triggering a Patroni reload and causing replica instability.

## Solution

Add a content-diff guard to `render_file()` in `cluster.py`: before writing, compare the new content against the existing file. If identical, skip the write and return `False`. Propagate this return value through `render_patroni_yml_file()` (now returns `bool`) up to `update_config()` and `_handle_postgresql_restart_need()` in `charm.py`, which now skips the Patroni reload (SIGHUP) when the configuration file is unchanged.

### Test results — 10 switchovers

| Unit | Unfixed | Fixed | Difference |
|------|---------|-------|------------|
| postgresql/0 (primary) | 29 | 22 | -7 (24% fewer) |
| postgresql/1 (replica) | 13 | 13 | 0 |
| postgresql/2 (replica) | 10 | 10 | 0 |
| **Total** | **52** | **45** | **-7 (13% fewer)** |

The reduction is on the primary, where post-switchover hooks rewrite an unchanged config. Replicas show no difference because switchovers genuinely change their `primary_conninfo`, making the SIGHUP legitimate.

The fix's primary value is in **hook storm scenarios** (the production incident case): when N hooks fire without any config change, the unfixed charm sends N SIGHUPs per unit; the fixed charm sends 0.

## Checklist
- [ ] I have added or updated any relevant documentation.
- [ ] I have cleaned any remaining cloud resources from my accounts.
