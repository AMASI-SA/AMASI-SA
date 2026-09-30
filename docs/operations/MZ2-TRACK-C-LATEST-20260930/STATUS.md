# Track C latest Production reconciliation
Status: WIP, source-only. Supersedes the earlier Track C evidence only after fresh gates.
Base J: fab6ce6bb4f533683d414ffec2289b0b976f386f
Base tree: 2b2e637fdb70fdd886c6c97e41711ec4f351e9fa
Branch: codex/mz2-cutover-latest-source-a-v2-20260930
A: 2c7591f6764b28519beae3f09446fd42104bff2d
B track: 4d0e74c32592e99bfe9ef820e51b695d93adb34e
Old integration: f0bba11bbe58efef92e4cdf30452e6e6016451ec (not reused as final head).

Ordered non-fast-forward integration of Track A then Track B; replayed three integration-only commits, retaining all four corrections. No textual conflicts. Qoyod recovery_campaign.py and tracked release intent remain byte-identical to latest Production. Prior reports are historical, not evidence for this new head.

Historical initial checkpoint only (superseded): compare exact Qoyod suite on pristine latest Production and candidate, diagnose isolation only if proven; run all source gates. No new intent before all source gates pass. Then freeze Source A, generate new intent-only Candidate B and run Host Node20 clean-clone rehearsal. No Production merge/deploy/publish, live DB writes, Post, activation, write-control changes, replay or backfill. Smoke B and deployment drift remain separate gates.

## Reconciliation and harness resolution
The final source is reapplied as an exact aggregate delta onto J, preserving the verified merged source bytes while avoiding merge-parent intent history. The earlier non-fast-forward rehearsal is not the final candidate. No intent bytes from old Track C were applied.
Original exact Production and candidate Qoyod selection: identical 8 failed/67 passed/2 deselected. Isolated controls reproduced the same failures. Root cause: July 5 fixture dates fell outside a rolling 60-day window when using the September wall clock. A test-only fixture passes the existing now parameter as July 12 in the two affected modules. All assertions remain unchanged. Same repaired harness yields 77 passed, zero failures/errors/skips/deselections on pristine exact Production and candidate. Runtime Qoyod content remains identical to J.
Original Auth/Preparation/Supplier workflows gain workflow_dispatch only so the mandatory gates can run against exact source HEAD without triggering intent generation. A+B CI additionally proves a fresh-clone source build and release-contract unit tests without freezing intent.
Fresh final-source CI results and exact identity will be recorded in Issue #1006 after the source is frozen, avoiding a self-referential source SHA. Until those results pass, no Candidate B is authorized.
Safety: Production/Preview writes 0; no Production merge, deployment, publish, Post, activation, write-control change, replay or backfill. P02 locked; G47 unactivated.
