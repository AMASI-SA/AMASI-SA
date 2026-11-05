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
## Assignment page only (2026-11-05)
User narrowed the change to the waiting supplier assignment list within My Products. This branch starts from production f1f8bba872c6b68cf5cf297a0caa379cc30db881, without the WIP overview redesign. Keep the existing summary and other stage routes. The old 47-second observation measured the overview, not isolated assignment latency.
New authenticated waiting-page handler returns up to ten eligible physical pieces and a continuation cursor. Bounded cursor batches are checked against current order status. Tenant, responsible employee and operational eligibility are enforced. Existing write routes remain authoritative. No customer records or production writes used for these tests.
Mobile companion on staff65 only requests a further page on user action and retains selection across visited pages. Focused backend tests: 38 passed. Mobile type check, waiting contract (32 checks) and on-demand screen harness passed. Production has not changed. Exact Mongo HTTP test, governed release rehearsal, signed APK and exclusive assignment latency measurement pending.

## Monitoring chronology follow-up (WIP, publication HOLD)
Canonical physical-piece projection now exposes its stored assigned_at, which native mappers already expect. No substitution from file/status/supplier timestamps. Two focused projection tests pass. Mobile companion oldest-first dates and read-only product images verified locally; notification conversations, urgency lifecycle and same-stage count parity still pending. No production change. User requested more fixes before publication.


## Preserved notification WIP, 2026-11-06
User reauthorized release of the priority app fixes. Broader monitoring urgency, conversation read markers and completion/push work is preserved separately before release integration. Not deployed; do not treat as final-ready. Backend focused run: 94 passed/1 failed/2 subtests passed; sole courier cycle fixture expects9000 while returns9600, baseline not yet characterized. Last small receipt lifecycle correction needs a focused rerun. Original Qoyod checkout and untracked build-performance outputs preserved. Release will integrate reviewed source from latest origin/hotfix/prod-snap-meta-final, not deploy this unverified WIP directly.
