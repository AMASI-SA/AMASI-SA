# Employee loading follow-up — WIP, not deployed

Parent web publication #1747 is verified at deployment
84bd03dcd585f57b2b08bf93c8ac0fd8beb58463. User reports approximately 35%
improvement on web and app; this is user observation, not an automated benchmark.

Priority: My Products, waiting/reviewed queues and supplier assignment. Ready and
shipping remain deferred. Installed Android is 1.0.50/code65. Prior measured
My Products and waiting supplier lists each took approximately 24–28 seconds;
pending review list 5–8 seconds and reviewed 10–14 seconds. These are sparse
emulator observations, not p95. One explicitly authorized review completion
succeeded and was read back in the reviewed catalog; no further business writes.

Backend candidate on codex/employee-loading-indexes-20261104: supplier dispatch
router shares successful index initialization across its requests. Six repeated
DDL commands are removed from warm workspace loads and dispatch actions. All
permission/current-status checks and business reads remain fresh. Concurrent
initial reads wait; failed/cancelled setup retries; different routers initialize
independently. Existing receiving router uses the same pattern.

Verification: baseline lifecycle regressions 3 failed/1 passed for repeated DDL;
candidate lifecycle + existing dispatch + quantity guard 38 passed (exit0).
Real-Mongo tests cannot run locally because no loopback Mongo is running;
extended workflow includes new lifecycle tests plus an HTTP/Mongo freshness
regression. CI pending. Production latency benefit unmeasured. Preview DB has
30 preparation pieces and is not established as production; its 4ms ping must
not be used to explain production latency.

Mobile candidate remains separately pushed at 980d7b2, decoupling workspace from
receiving notice. Fresh TypeScript passes, lint has 0 errors/44 warnings, focused
tests34+45 pass. Full legacy check hits Windows quoting then an existing Build44
owner assertion (amasi-gulf vs amasi-sa); it is not all-green. No new APK built.

Next: verify exact source CI including isolated real Mongo, review diff, then
governed v5 release intent/rehearsal before authorized publication. Shared /app
guard last read inactive; no source changes or lease created. Untracked shared
files and original Qoyod checkout preserved. Production changed by this
follow-up: NO.

Integrated current Production972777b46 after concurrent PR1748 advanced the base.
No overlapping supplier-dispatch changes. Focused38 tests passed again. Previous
source9c4491e5294a3353591ae44f26a4559e0c465fe1 real-Mongo CI41875021940
passed161 tests. Its readiness failed because Production advanced, not a build
execution failure. New exact-source CI required. Issue1006 rejects comments at
GitHub2500-comment limit; continuation is retained in PR1749 and this document.

## Frozen release checkpoint
Current release PR is1750 (1749 superseded/closed). Branch
codex/employee-loading-release-20261104 starts directly from972777b46;
source A e725e3baf4575327d8c3bbfab578b9b1afbdebbd;
intent-only B bf6ee3d22e05924c4694416ddec823f4cce36106.
A..B changes only release/release-intent-v5.json. CI source readiness41878411004
succeeded; downloaded intent SHA256 de41dacf2a4ab913dc1c1dba13918b56c8a92449fed383f1041124a5e7fe227c.
Frozen-B checks remain outstanding; requested explicit readiness workflow.

Actual Emergent source A governed A/B build succeeded in isolated
/tmp/performance-1750-rehearsal. Initial /root clone installation failed ENOSPC:
/root shares the9.8GB /app volume. Moved only this task's324MB clone to/tmp,
removed its partial node_modules there and repeated successfully. /app free
space320MB afterward. No production source edits. Terminal last freeze/verify
command outcome is unknown because browser tool timed out then disappeared
from available tools. Must inspect terminal before retrying. No lease prepared,
no merge, no publish. Current /app lease last observed inactive; recheck fresh.

Mobile source d48ae44e7b00da151e4cb1366cdd9a4fbf8e4ed0 is Draft PR332,
based on exact staff65. Android bundle export passed; no new APK/OTA/install.
User separately authorized one specific review-completion attempt after current
work; handle through ADB with exact-order verification and GET/readback before
any retry. Do not record customer identifiers in this repository.

Next: finish frozen-B CI and restore supported browser/terminal control;
inspect isolated build state, run full adapter rehearsal, then fresh release
status/prepare/prepublish and publication/verify. Publication still NOT complete.

Frozen-B readiness41887012007 completed SUCCESS, including clean-clone adapters
on Node20.20.2 and24.19.0. Mobile PR332 frontend-checks/session succeeded;
branch-filtered combined bundle job skipped, with separate local staff-profile
Android export already passed. No merge or production publication: supported
browser/computer-control tools remain unavailable in this session. Resume from
frozenB, inspect existing terminal before rerunning its unknown last command,
and freshly check guard/remote base. ADB had no device after tooling restart;
started existing Mezan-Operational-UAT without wipe, reconnected authorizing
transport; device is visible but boot/shell readiness still pending.

## Current release checkpoint: PR1752
Production advanced to20c47efffa3efdf1afa29b6312ceaa8d261070cf, so PR1750 was closed without merge. Current branch codex/employee-loading-prod20c-20261104 starts directly at that base. Source A f23a6967b543dac9d2fce98ea0cb0144a1bda733 is pushed in PR1752. Focused38 tests pass. Source readiness41891286950 and waiting-review41891286868 passed; last observed CodeQL and stock eligibility still pending.
Intent-only B ac9e8b3c6fcd2a8cf8d20a04b72eba86bdd008d8 is committed locally from the successful readiness artifact; source and base identity verified. A..B changes only release/release-intent-v5.json. Push failed with GitHub DNS/connectivity errors; remote B presence is unverified. Durable fallback patch: D:/codex-artifacts/performance-20261104/0001-release-freeze-employee-loading-source-f23a6967.patch.
User explicitly authorized merge and publish. No merge/publish/lease performed for this slice. Browser/computer-control tools remain unavailable. Bundle work deferred by user; no bundle fixes in release. Production changed by this slice: NO.
Next: verify remote branch before retrying B push, finish B checks and clean-clone rehearsal, verify latest production base still20c47, merge A/B without squash/rebase. Then restore supported Emergent access, check guard status, update /app preserving unrelated files, run rehearsal/prepare/prepublish, publish and require explicit newer Deployment Succeeded plus guard verify three probes. No new mobile APK published.

## Merged checkpoint
Retry push succeeded for frozen B ac9e8b3c6fcd2a8cf8d20a04b72eba86bdd008d8 and this evidence branch. All applicable B CI passed, including CodeQL41894563971, readiness41894563679 and41894625650 (both clean-clone adapter host versions), and waiting-review41894563752 (161 Mongo HTTP tests passed). Required checks passed. Production base was freshly fetched and remained20c47efffa3efdf1afa29b6312ceaa8d261070cf immediately before merging.
PR1752 merged normally (no squash/rebase) at2026-11-04T19:18:31Z, deployment commit cec4c3854031c357577f07568f91a44c082905fc. Merge tree equals frozen B. Source A remains f23a6967b543dac9d2fce98ea0cb0144a1bda733. GitHub production branch changed; LIVE production publication by this task: NO. No lease created. Supported browser/computer tools still absent from current tool catalog, so cannot inspect/update /app or click publication.
Next: restore supported Emergent browser access, freshly inspect guard status and /app state, preserve unrelated files, sync exact current reviewed production source, rehearse frozen adapter then prepare/prepublish and publish. Completion requires newer explicit Deployment Succeeded and guard verify three successful identity probes plus inactive status. Do not claim live deployment from this merge.
