# C1 Stage 7 connected browser acceptance

Outcome: five C1 browser scenarios passed after the root-owned mobile wrapping correction. The unchanged original 23 scenarios also passed with C1 mode disabled. This is source/UI integration acceptance only: no production Smoke B, full 16-stage business UAT, release, opening post, financial write, or activation was performed.

Environment: Windows Edge headless; Node 22.23.2; real FastAPI router and default frontend HTTP transport; disposable UUID Mongo databases on `127.0.0.1:27130`, replica `mz2c`; loopback HTTP `127.0.0.1:18767`. Dotenv disabled and synthetic-only JWT/database settings. Actual tested source hashes and HEAD are in `source-manifest.json` (working-tree wrapping correction included).

## Verified browser behavior

1. Canonical courier selection and exact rich terms save through the installed API: independent shipping/commission VAT treatments, postpaid mode, fractional commission 0.01, fixed fee, aware Riyadh effective time. Opening amounts/bank are absent from terms.
2. Retained original downloaded through the real authenticated endpoint; downloaded SHA256 equals the fixture's immutable bytes. Accountant explicitly reviews contract, shipping tax, and commission tax separately. No approved proof is seeded.
3. Human-readable persisted terms are reviewed and approved with the actual draft hash/evidence IDs; Stage 7 readiness becomes true.
4. Browser reload reads back the same approved contract. Desktop/mobile screenshots captured; document width fits the 390px mobile viewport after the correction.
5. UI revocation persists, and Stage 7 readiness becomes false with `shipping_evidence_not_approved`. Only onboarding sessions and native shipping setup may differ from baseline. Every other collection, including source bytes, financial journals/accounts, controls, operational inputs, and inventory, remains unchanged. No external request or unexpected browser error occurred.

## Evidence

- `final/browser/browser-c1-results.json`: five PASS results, HTTP requests/responses, final full-database fingerprint comparison.
- `final/browser/c1-approved-desktop.png`, `c1-approved-mobile.png`, `c1-revoked-mobile.png`.
- `final/browser/retained-original.bin`: synthetic downloaded original.
- `default23/browser/browser-results.json`: original 23 scenarios PASS; original browser.cjs unchanged.
- `browser/browser-c1-results.json`, `browser.log`, `mobile-overflow.json`: preserved initial failure. Revoke buttons with long evidence IDs were 532-554px wide at390px viewport; document width600. Root corrected product wrapping. The failure was not waived or hidden.
- `revocation-after-mobile-failure.json`: separately labelled continuation proving revocation before the wrapping correction; not used to override the failed mobile assertion.
- `final/cleanup-proof.json`, `default23/cleanup-proof.json`: each run's two UUID databases confirmed absent after graceful shutdown.
- `process-cleanup.json`: all six task-owned launcher/server PIDs absent; loopback port18767 no listener. Mongo service was not stopped.
- `c1-browser-harness.patch`: durable uncommitted harness changes only.

## Commands

With `MZ2_AB_RICH_SHIPPING=1`, `MZ2_AB_DIST=<evidence>/final/dist`:

`node scripts/testing/mz2_onboarding_ab/build.cjs`

Task-owned hidden Python runner starts `uvicorn.Server` for `scripts.testing.mz2_onboarding_ab.server:app`, host127.0.0.1 port18767; its stop-file watcher sets `server.should_exit` for graceful fixture cleanup.

With `MZ2_AB_ORIGIN=http://127.0.0.1:18767`, `MZ2_AB_OUTPUT=<evidence>/final/browser`, local Playwright module and `MZ2_BROWSER_CHANNEL=msedge`:

`node scripts/testing/mz2_onboarding_ab/browser-c1.cjs` — exit0, five PASS.

Repeat build/server with `MZ2_AB_RICH_SHIPPING=0`, distinct output directory and fresh databases:

`node scripts/testing/mz2_onboarding_ab/browser.cjs` — exit0, 23 PASS.

`git diff --check -- scripts/testing/mz2_onboarding_ab` — exit0.

No commits or pushes were made by this agent. Harness write-set: server.py, build.cjs, review.jsx, README.md, new browser-c1.cjs. Product mobile fix belongs to root.
