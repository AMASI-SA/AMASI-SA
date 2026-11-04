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
