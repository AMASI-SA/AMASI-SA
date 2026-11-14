# Campaign operating allocation: executive parity

Task: CAMPAIGN-OPERATING-EXECUTIVE-PARITY-20261114

## User-visible behavior

The campaign card must show direct order costs, operating expenses allocated with the full executive expense/sales ratio, and net profit after advertising, direct costs and allocated operations. Missing direct costs stay explicit and negative profit is retained. The percentage is dynamic, not a fixed 3%.

## Source correction

The published PR #1920 exposes all three rows. Read-only live acceptance found operating/net unavailable while the executive operating row was numeric; the exact live cause remains unproven. Source review established legitimate-zero recurring expense and payroll scope differences. This candidate reuses the executive recurring contribution, validates explicit invalid zero-fallback estimates, filters excluded/noncontributing employees before cost validation, and preserves the operating reader's structured timeout. Positive actual/historical invoice evidence keeps precedence. All existing owner scope, duplicate checks, caps and deadlines remain.

No provider, customer, order, accounting, advertising, budget, bid, scheduling, or synchronization writes are added.

## Upstream preservation and fresh release lineage

PR #1921 advanced production to J3 `94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894` (tree `d636970f1a954530cf7845e408e05362af160cfa`) while the preceding candidate was in CI. Its financial-center changes are preserved in full. This fresh source commit is an ordinary child of J3 and copies exactly the three reviewed backend/test blobs from A2 `0f5bca7427fed7851008d0f7d64e23414746bdc1`; J3's tracked intent is unchanged at this source stage.

Frozen PR #1922, source A2 and intent B2 `afe6ff699e0de1601b37d2fd81e4291cb69dc75f` are retained for evidence, never rebased or merged against the changed base. A new A3 candidate and intent-only B3 must be built and reviewed from this lineage.

## Verification and coordination

The previous A2 passed independent static review, 248 backend tests, 50 frontend tests in 9 suites, and all 11 applicable workflows. B2 host 20.20.2/24.19.0 adapter rehearsals passed with all 293 generated files byte-identical. Those results document the previous candidate; fresh A3/B3 workflows and artifacts are still required.

Original /app was observed at J3 with tracked-clean state and official guard inactive at 2026-11-14T16:41:25.665266Z. Another conversation is completing PR #1921's release. This task has not changed its workspace, created a lease, or published the follow-up. Do not interfere with that release. Before any original-workspace change or rehearsal, require fresh inactive status and current production/ledger agreement. Complete exact A3 CI, freeze only its generated reviewed intent, require exact B3 gates and both host proofs, then ordinary tree-equivalent merge and the official release sequence.

Durable operator checkpoint: `codex/campaign-hover-profit-release-handoff-20261114`. Keep checkpoint commits separate from source and intent lineage. PR #1920's successful publication and three-probe verification are complete and must not be repeated.
