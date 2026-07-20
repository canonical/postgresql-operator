# VM charm update_config adoption — SDD ledger

Branch: skl-01-adopt-update-config (off flip/tls-lib, #1816 head). Draft-only, stacked.
Lib pinned at archive 08f5993 (lib PR-stack tip). Goal: prove the migrated update_config
behaves correctly when the VM charm consumes it (validation).

## RESUMED 2026-07-11

- **Task 1: COMPLETE** — commit `2335bf3` (dependency archive pin). Lib installs; migrated
  7-arg ConfigManager signature verified. `tox -e unit` install path works.

- **Task 2 charm.py: VERIFIED SOUND (2026-07-11).** Reviewed the WIP: 7-arg ConfigManager
  built after tls_manager (all args right); 3 bridges match original charm code; delegation
  passes all 6 rel-handler expressions VERBATIM from the old render call; deleted methods have
  zero surviving callers. Kept `_can_connect_to_postgresql`/`cpu_count` (still used). WIP is
  good — proceeding to test reconciliation, not discarding.
- **Task 2b: COMPLETE** — commit `ffe6a0f` `refactor(config): adopt single-kernel update_config,
  drop charm copy`. 193 passed / 0 failed; 31 tests deleted, 4 added, 1 incidental patch stripped;
  tox unit + lint green; signed, canonical identity. Adoption diff: 74 ins / 1707 del across
  charm.py + test_charm.py + template. Review package: `~/.claude/tmp/skl-01-vm-review-package.txt`.
- **Task 2 REVIEW: CLEAN (opus).** Spec ✅, quality Approved. Independently re-ran 193 passed;
  delegation behaviour-preserving (6 rel-handler exprs verbatim, refresh load-bearing); bridges
  faithful; deletions safe; 4 new tests real (red under mutation). NO charm-side fix needed.
  Flagged 3 LIB-SIDE test-coverage gaps (debt, not charm regressions): (1) `get_available_memory`
  MemTotal*1024 parsing untested (lib workload tests mock it) — most valuable to close; (2) worker-calc
  all-None→{} and zero-cpu→"0" edges; (3) enable_tls render-kwarg differentiation. Lib code is faithful;
  only lost test ASSERTIONS. Close in the lib stack (#182 owns workload/params) or track as debt.
- **VM unit adoption: DONE.** Draft PR canonical/postgresql-operator#1849 (base flip/tls-lib,
  stacked on #1816), host-verified, both commits signed. This is the unit-level migration proof.
- **Remaining:** K8s adoption (#1619, same recipe); integration validation (deploy+reconfigure,
  infra-gated); lib-test-gap decision; final global migration skill.

- **(historical) Task 2 partial WIP** (implementer stopped mid-run):
  - DONE (uncommitted): `src/charm.py` rewired — 27 ins / 384 del (7-arg ConfigManager
    construction + 3 bridges + update_config delegation + dead-code deletion). `templates/
    patroni.yml.j2` deleted (staged).
  - NOT DONE: test reconciliation (tests/unit/test_charm.py still references deleted
    methods -> suite is RED), `tox -e unit`/`tox -e lint` never run, NOT committed.
  - Backup of the partial diff: `~/.claude/tmp/skl-01-vm-task2-charm-wip.patch`.
  - Requirements: brief `~/.claude/tmp/skl-01-vm-task2-adopt-brief.md`, map
    `~/.claude/tmp/skl-01-vm-adoption-map.md`.

### RESUME STEPS (tomorrow)
1. `git -C <worktree> diff HEAD` — REVIEW the partial charm.py WIP against brief sec A-D
   (construction reorder, 3 bridges, delegation, deletions with caller-checks).
2. If sound -> finish brief sec E (test reconciliation: delete the migrated-away tests, add
   the 3 bridge tests + 1 delegation test), then `tox -e unit` (GREEN = migration proof) +
   `tox -e lint`, then ONE commit `refactor(config): adopt single-kernel update_config, drop
   charm copy` (signed, signoff marcelo.neppel@canonical.com, no co-author).
   If NOT sound -> discard (`git checkout src/charm.py; git restore --staged --worktree
   templates/patroni.yml.j2`) and re-dispatch the implementer with the same brief.
3. Task 3 (planned): integration validation (deploy + reconfigure) — infra-gated.

## Broader context (both charms)
After VM lands: repeat for K8s (#1619, head `test/async-replication-tls`). Lib PR stack:
#181->#182->#183->#180 (draft, on tls-4-tests/#175). Final deliverable: a global skill
codifying this whole per-module migration recipe (memory project-migration-skill-deliverable).
