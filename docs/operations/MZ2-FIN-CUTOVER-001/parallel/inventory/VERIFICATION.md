# Verification record — R1/R2/R3 correction, 2026-09-20

Reviewed local HEAD and fetched remote both exactly 45d8f9499cf3218e61a6ef6898bff55adcdba57c; worktree clean on codex/mz2-inventory-prep-20260920, same Draft PR #1107. Scope: preparation files only. Base ebf92e6606a810a1e3f0a0e2026984a5482f71bc; P01 contract e9d05b2be67a5ca54eb4e1b99a3a2a6fb16cf203 unchanged.

## 1. Executed arithmetic
Command from isolated inventory-prep worktree (bundled Python 3 runtime, -B disables bytecode):
```
python -B docs/operations/MZ2-FIN-CUTOVER-001/parallel/inventory/verify-preparation.py
```
Exit 0:
```
PASS: 7 synthetic scenarios, 31 balanced journals; exact expected deltas and stock.
PASS reconciliation: 16 event-level inventory account/pool deltas match stock detail.
PASS freight_split: receipt 10 units / 200 + value-only freight 60 = 10 units / 260 / average 26; outbound 40 excluded.
```

Receipt freight-goods-receipt-001 is newly created inside the synthetic scenario: Dr inventory200 / Cr GRNI200; quantity10/value200, accepted with synthetic delivery evidence. Allocation synthetic-freight-allocation-001 links the original transport cost key to this receipt and adds quantity0/value60. Final10/260/average26; outbound40 remains delivery expense. Source freight AP115/VAT15 occur once, payment clears AP only. These are synthetic tax assumptions, not an approved policy or opening balance.

## 2. Executed reconciliation and negative checks
Explicit stock_mapping binds item to cost_pool/account; each inventory GL leg carries cost_pool, each stock row carries item/cost_pool. For every event, signed GL inventory delta must equal detail value delta for that account/pool BEFORE independent expected-total checks. This covers inventory/materials/FG here, not a full WIP subledger implementation.

Allocation validates a preceding evidenced accepted receipt for the same item/pool, preceding documented cost origin with a funded inbound allowance, allocation identity and amount cap, value-only movement, and exactly inventory debit/acquisition-clearing credit. It cannot repeat freight payable/bank/VAT.

Executed independently using the command above plus each option:

| Option | Actual exit | Actual output |
|---|---:|---|
| --negative-case missing-stock-value | 1 | REJECTED: inventory_detail_mismatch |
| --negative-case invalid-receipt | 1 | REJECTED: invalid_allocation_receipt |
| --negative-case invalid-origin | 1 | REJECTED: invalid_cost_origin |

The first removes only inbound_allocation.stock; all GL legs remain balanced and expectations unchanged. It fails cross-ledger reconciliation, not an expected-total assertion. The other mutations change only the reference. Negative runs alter memory copies, never fixture files.

The command with --negative-tests also exited0, printing these three lines followed by the three positive PASS lines above:

```
PASS negative missing-stock-value: balanced GL rejected with inventory_detail_mismatch.
PASS negative invalid-receipt: balanced GL rejected with invalid_allocation_receipt.
PASS negative invalid-origin: balanced GL rejected with invalid_cost_origin.
```

The harness explicitly checks that every negative journal is still balanced, requires the exact rejection reason, and fails if the broken example is accepted. These are offline data-contract checks, not Mongo dedupe/transaction proof.

## Actual shipping reference comparison
Read via git show at 7ee877a0b19e12b997d2f7e5fe484b0ede9b86ff, [PR #1108](https://github.com/AMASI-SA/AMASI-SA/pull/1108), [INTEGRATION-CONTRACTS.md](https://github.com/AMASI-SA/AMASI-SA/blob/7ee877a0b19e12b997d2f7e5fe484b0ede9b86ff/docs/operations/MZ2-FIN-CUTOVER-001/parallel/shipping/INTEGRATION-CONTRACTS.md). Compared only the common transport boundary: owner/origin key, exclusive purpose, conserved allocation, actual receipt and no repeated payable/bank/VAT. Shipping20/10 of30 and inventory60/40 of100 share that boundary. This is NOT full shipping approval or P01 approval. D01–D11 retained unchanged.

## 3. Not executed / historical evidence
- Existing test_iter250b_phase4_product_cost_update.py: fixture loads backend/.env and writes MONGO_URL/DB_NAME. No isolated database authorized/configured by this task.
- HTTP/ASGI, real Mongo transaction/fault/recovery, browser UI, legacy shutdown acceptance, full application tests/build: NOT RUN.
- Preview switch/financial writes, production writes, index/migration/setting changes, merge, release lease, deployment: NONE.
- No claim that P03 exists or is accepted; all operational scenarios remain plans.
- No app imports/.env/Mongo/provider access in these checks. The old AST helper probe was executed at45d8f949 only and NOT rerun for R1–R3; now optional via --legacy-helper-probe, its function body unchanged. Historical average6.5/retry6.2 observations are not fresh results of this correction.

## File boundary verification
Only five existing preparation files modified: synthetic-fixtures.json, verify-preparation.py, ACCEPTANCE.md, VERIFICATION.md and CONTRACTS.md, all under parallel/inventory/. Inspect git diff --check and exact names before commit; immutable correction SHA belongs in PR/Issue handoff. No shipping/core/STATUS/release files changed.

P01 remains open. P02/P03 remain locked. STATUS.json, source, shared permission files, product/order cost files and release files remain untouched.
