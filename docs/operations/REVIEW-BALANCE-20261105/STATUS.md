# Paid order review regression — not deployed

User reports review worked on October 30 and later deployments blocked paid orders. Code history introduces PaymentDTO.source_balance in b6c63ea67 (November 4). Commit timestamps alone do not prove production deployment timing.

Confirmed reproduction: repository mapping suppresses source_balance while g47_salla_snapshot.component_pending is true. Local review's own component acknowledgement clears that flag before final validation. Native payment remains unchanged, but the derived DTO balance appears. The business snapshot rejects /order/payment/source_balance as unknown_source_change and rolls back completion. A real ASGI/Mongo transaction reproduces HTTP409 under normalization v2, with equal approved/current source hashes and stage finalize. New normalization succeeds and creates exactly one review event.

Fix: normalization v3 excludes only DTO payment.source_balance from review scope v2. Native provider fields and canonical paid/due/method remain guarded; scope v1 is unchanged. Existing normalization-v2 evidence remains valid and is compared using its original normalization, never silently upgraded. Open displayed approval tokens may require a fresh read after rollout. Existing uncertain attempts still require exact readback/fenced closure; never blindly replay.

Diagnostics: retain the already sanitized nested source_diagnostic allowlist in review_completion_409 logs. Previously only top-level fields were retained, dropping comparison hashes and paths. No raw order or customer values are logged.

Fresh evidence: mapper regression failed before with unknown_source_change at /order/payment/source_balance. Mapper/snapshot suites then passed84 tests plus11 subtests. Real local review transaction passed for mada; intentionally rerunning normalization v2 produced the same409. Expanded mada/tabby/tamara and adjacent review/assembly tests are running; final outcome must be recorded before release. Observability/snapshot tests passed20 plus11 subtests. No production release prepared.

Device UAT before this fix: Android67,1.0.52; ten unique single submissions for fully paid non-Riyadh November3 orders. Explicit errors:297800019 and297802246 unknown_source_change;297784779 component_source_event_stale. Remaining submissions297800691,297788851,297786657,297806933,297788398,297784666,297782862 did not have confirmed final proof captured. Do not mark them completed or replay them from this note. Local evidence: D:/codex-artifacts/performance-20261104/review-paid-nonriyadh-results.jsonl. Production source diagnostics for those exact incidents remain unavailable, so do not claim this reproduction explains every individual rejection.

Isolated Mongo replica set reviewBalanceUat on127.0.0.1:27859, data D:/codex-test-runtime/review-balance-20261105/data; own mongod PID388. Preserve unrelated Mongo processes. Test databases are unique synthetic fixtures and cleaned by their test teardown.
