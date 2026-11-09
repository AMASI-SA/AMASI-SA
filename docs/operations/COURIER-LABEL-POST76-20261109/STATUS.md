# Courier label refresh after Android76

Owner reports the latest deployment works well except intermittent store-courier
label printing. Scope is only that printing failure; retain Android76/PR1838 and
all unrelated production changes.

## Verified starting point

- Production deployment commit: `10b25d2ad75fb3f1d82fe43ecb64b34f9a410a01`.
- Task branch: `codex/courier-label-post76-20261109`.
- Source A: `f8af8c2027f1512aa5377933e5229085f375451e`.
- Frozen intent-only B: `ca2c494178da3543570afa29371766cca1726a4e`.
- B tree: `05ac32be6d7d6b9e86b4a94e8edf0c1e3ed4b158`.
- Runtime: `rg5-e80fcfae723c5e865a69dba84df03e1ecdff7faaae323e464d5cc93aee3a420b`.
- PR1839 is ready for review, unmerged. Do not rewrite frozen A/B.
- This final status checkpoint is on evidence-only branch
  `codex/courier-label-post76-evidence-20261109`; never deploy/merge it.
- Latest observed shared guard: `active:false`, matching production HEAD.
- No live business mutations used for testing. No new lease or publication.

## Root cause and narrow change

The explicit completion-and-label action captures shipping before its completed
POST. A same-courier order webhook advances `carrier_updated_at`, causing the
internal document fence to emit `shipping_snapshot_changed`; the outer full
snapshot comparison can reject the same update after document creation.

Only the explicit store-courier action permits observation clocks/provenance to
change. Its outer material fence still compares carrier identity, shipment,
address, items, custody, holds, workflow assignment and pending refresh flags.
Ordinary printing and external-carrier fences retain their current behavior.
One initial order GET and one explicit completed POST remain; no polling,
additional status GET or automatic status POST retry. No Android change.

Changed code: `assembly_manual_label_refresh.py`,
`order_engine/amasi_shipping_compat.py`,
`order_engine/shipping_label_service.py`.
Regression: `tests/test_manual_courier_label_refresh_race.py`, included in the
existing Review Completion Resumable CI workflow.

## Verification

- Exact pre-fix regression: 4 failed / 11 passed. Both in-progress and already
  completed orders reproduce the exact stale-label failure, during status POST
  and immediately after document creation.
- Post-fix combined focused local run: 115 passed, 23 skipped (real Mongo absent),
  64 subtests passed. Command:
  `PYTHONPATH=backend:backend/tests:<isolated-test-deps> python -m pytest -q --asyncio-mode=auto backend/tests/test_manual_courier_label_refresh_race.py backend/tests/test_mobile_assembly_refresh_actions.py backend/tests/test_amasi_shipping_compat.py backend/tests/test_unified_completed_carrier_label.py backend/tests/test_shipping_saved_label_print.py backend/tests/test_assembly_print_completion.py --disable-warnings`.
- Includes 40 sequential explicit refreshes with no retained lease, material
  mutation rejection, and unchanged external observation fence.
- Independent read-only review found no concrete blocker; independent focused
  run passed 78 tests / 59 subtests before final local run.
- Live exact-order diagnostics not obtained: shared terminal dependency/runtime
  unavailable followed by editor disconnection. Screenshots and deterministic
  code reproduction prove the defect, not the private live order contents.

## Final exact-B CI — 2026-11-08 23:40 UTC

All 14 applicable workflows passed on exact B; the unrelated conditional TikTok
workflow skipped. Reviewed PR1839 is mergeable and no longer draft.

- Review Completion Resumable42535555847: 247 operation cases / 59 subtests,
  658 retained-provider cases / 345 subtests, 37 recovery unit cases and 1
  acceptance guard passed. The executed-case checks confirm zero skips/failures.
  Operation group includes the new courier race and 40-cycle regression.
- Shipping Print Boundary42535555856: 26 focused cases plus 579 regressions
  passed; 1 existing conditional skip.
- Release readiness42535555893 passed including actual Host Node20.20.2 and
  24.19.0 clean-clone/gitless adapter, isolated packages and HTTP JSON identity.
- Intent artifact13638933186 verified ZIP
  SHA2560b6a0b8a87cfae2472629c4fda81ddd7d6c452b1e16f18866515ab6ff7b9984a;
  exact intent blob7b9783e7e4847079ab3c58beba67ac8abd2a73e8.
- Public health still reports previous published Android76 server source
  6a1c18d4d87a92cd7a473db1eb8aaba33ae7fc85 and
  rg5-b27aabe2e6917c998c4bb3f13eb9c6f979797954a6e74d4edf4a2cf8692e5b6a.
  This single read is continuity evidence, not a fresh3-probe release acceptance.

## Blocker and next safe action

The shared development workspace stopped from inactivity. Code Server changed
from502 to explicit Preview Unavailable: agent resting after inactivity; restart
from Emergent. Existing and fresh management project/home tabs time out, including
after reload. No sign-in or payment action is required/attempted; no paid chat.

Wake the existing Salla Analytics development environment through Emergent
management. Then FIRST run in its terminal:
`cd /app && python scripts/production_release_guard.py status && git rev-parse HEAD`.

Require inactive guard and inspect tracked cleanliness/unrelated files. Fetch
latest production and exact1839 B; if production advanced, re-evaluate ancestry
and governed A/B before merge. Do not clear another lease. Use normal merge,
preserve sourceA and intentB, fast-forward shared/app only, run its governed
adapter, then owned prepare/prepublish, authorized publish and verify3/statusfalse.

The owned local checkout is clean at B. No shared/app mutation, new lease, merge
or publication was performed for1839. Production changed by this follow-up:
**NO**. No new APK required; physical printing acceptance still follows deployment.


## Continuation — 2026-11-09 UTC (supersedes prior blocker)

Owner continued. Environment woke; fresh guard inactive and tracked /app clean.
PR1839 merged normally:18c245748d22615332d10c674812d4362b88ae71; /app fast-forwarded.
First shared adapter failed ignored backend __pycache__; safely quarantined in
/app/.release-preserved/courier1839-bytecode. Second failed ENOSPC: /app9.8G full.
Preserved 3 inactive worktrees with per-file SHA verified tar.gz copies in
/app/.release-preserved/courier1839-space; relocated originals into
/tmp/courier1839-preserved-worktrees and retained original paths as symlinks:
p01-write-final-preview-20260920 (archive43128878bytes),
p01-intake-runtime-20260919 (17554248bytes),
preview-runtime-state-20260919 (104440901bytes).
Active p01-acceptance Mongo untouched. Free space became913M.
Third governed /app adapter PASSED63.10s, exact source and rg5-e80fcfae identity;
isolated backend1161files/health_verified/isolated_verified true.
Log /tmp/courier1839-build.log, owned Terminal2 idle. No new lease or live publish.
Management home/options recovered but entering project /chat loads indefinitely;
AX snapshots repeatedly time out. Need open existing project Manage UI, then
fresh guard status, prepare --actor codex-courier-label-1839, prepublish, publish
once and verify3/statusfalse. Do not publish before lease/prepublish. All14 B CI
passed. Production runtime remains prior1838; source merge is not publication.
