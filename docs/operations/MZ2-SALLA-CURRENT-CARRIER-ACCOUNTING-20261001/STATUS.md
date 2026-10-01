# MZ2_SALLA_CURRENT_CARRIER — separate Accounting Integration

State: WIP checkpoint; no merge/deploy/Production writes. Operational PR #1231 stays unchanged and operational-only.

## Reviewed integration baseline and writer

- Base: Final Integration successor PR #1232, `codex/mz2-final-integration-commit-retry-20261001`.
- Baseline HEAD: `db6bd9c6b8941e758d58407749b60ed6aff22f5e`.
- Baseline TREE: `3229f42b67739dbc6b10dc5f83ea4028838b73d3`.
- Task branch: `fix/mz2-current-carrier-fee-latest-20261001`.
- Existing writer reused: `backend/accounting_shipping_native.py::accrue_fee` → `_post` → `backend/accounting_ledger_v2.py::post_journal_v2`, inside existing `atomic_owner` transaction. No new writer or Legacy fallback.
- Imported preparation reader also guarded: `backend/accounting_shipping_p02.py::prepare_courier_fee`. This does not authorize its retired financial writer.

## Source of truth and bounded adapter

Owner-scoped canonical `unified_orders.salla_shipping_current` from operational PR #1231 is authoritative. Exact configured carrier identity, delivered outbound shipment ID, AWB and sealed new-evidence proof must agree. Missing, ambiguous, inactive or mismatching evidence fails closed before rate selection/posting. Store Driver produces no Courier Company Fee event/journal. Existing store-driver fees and COD paths remain unchanged.

Only newly sealed native evidence can contain an optional current shipment proof. COD economic facts, existing evidence seals and historical posted fee/journal are unchanged. Old unposted native evidence without proof is not resealed automatically and cannot create a fee. Imported CSV without shipment ID cannot prove a replacement shipment even if its AWB is reused.

Posted idempotent replay precedes the guard and preserves prior amounts, rates and journals. Current eligible fees use the existing approved carrier-specific rate selector unchanged. Operational owner serialization from #1231 and existing accounting owner transaction share the same Mongo owner row.

## Verification at checkpoint

- Unit/mock adapter and imported-reader tests: 37 passed, exit 0, with pytest --noconftest; no Production data.
- First CI run36904397982 reproduced the actual baseline bug: old iMile evidence still created a fee after Store Driver change (DID NOT RAISE).
- Initial test-only composition incorrectly copied the whole operational repository file and erased the existing V2 source snapshot methods. Fixed the verification harness to apply only #1231's delta and assert that both V2 source snapshot methods remain byte-equivalent at AST level.
- Successor #1232 appeared during work. Its native writer/rates/evidence source bytes are identical to #1230; its atomic retry changes and all other work are preserved exactly. Original Draft #1233 is superseded by a new latest-base carrier; its checkpoint remains recoverable.
- Focused CI now includes A–I, 27 native/driver/ledger/atomic/operational suites, zero skips; final execution pending.
- Operational dependency pinned: HEAD `6507c813a73ea71a5eab2a8d39c5bb92a4075c70`, TREE `cb9507cbf79ce401a3381c3311355c0cd3effe05`. Its 19 checks passed (4 unrelated skips); genuine changed-carrier provider webhook remains unobserved.

Next safe action: configure isolated CI to test unchanged baseline regression plus candidate combined with the pinned operational source in a temporary validation checkout; run full existing native/driver/ledger adjacent contracts and all A–I cases with zero skips. Then review the exact immutable candidate and update Issue #1006.

Production changed: no. Production financial writes: 0. No merge, deploy, release lease, cutover, write-control, tariff or historical journal change.
