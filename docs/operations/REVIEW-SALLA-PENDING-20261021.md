# Review Salla pending guard — publication checkpoint

Owner confirmed: check only when approving local Mezan review. No page-load preflight, disabled-button change or extra background Salla requests. User authorized publication with rollback on 2026-10-21.

Repository AMASI-SA/AMASI-SA; branch codex/review-salla-pending-guard-20261021. Prior verified source 391971bad54a56883825c8811d7e882a2d61651b; production base remains 571e1704fc823c681c61b2f31de1db4f5ea5f55a. This checkpoint changes two test fixtures only, plus this record. Runtime guard unchanged.

Evidence: dedicated guard CI39508807677 passed 38 tests and 19 subtests; readiness39508807679 passed. Full review39508807577 failed, including synthetic provider reads forbidden by local-assembly fixture and leaked patches after failed setup. G47 CI39508807591 failed27 cases (300passed/118subtests), including eligibility fixture failures and13 inventory.receipts.write permission failures not yet classified against base. Security39508807682 failed backend and frontend dependency audits. No gate bypassed.

Fixture repair: shared isolated test provider supplies GET-only current fixture status to review route, so real guard remains exercised. Local assembly no longer replaces this read with a forbidden-all-I/O mock; its provider writes/refresh remain forbidden. Patch cleanup registered for failed setup. Python compile and diff check exit0; full CI pending.

Rollback source verification: inverse runtime patch applied in isolated temporary directory, both modified runtime files matched base byte-for-byte. New guard module is removed by task revert. This is source verification, NOT a runtime/deployment rollback rehearsal. No schema migrations. Preserve business data and unrelated changes. Revert task runtime diff on then-current production; create fresh v5 A/B intent and rehearse before rollback publication. Never hard-reset or reuse old intent.

Publication state: NOT MERGED, NOT DEPLOYED; Production changed NO. No release lease or intent created. No mobile/printing build cancelled. Page-load preflight experiment fully removed and never pushed.

Next: inspect new CI, resolve remaining integration failures and dependency audit gates, then check /app Release Guard status active:false, prepare/rehearse forward and rollback releases, publish under v5 and verify three exact identity probes. Do not claim readiness from focused tests alone. Remote Salla change after GET remains a cross-system race until locally observed. Issue1006 is full; task doc and PR1455 are handoff records.
