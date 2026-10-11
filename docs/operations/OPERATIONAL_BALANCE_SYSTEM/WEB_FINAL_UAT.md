# OPERATIONAL_BALANCE_WEB_FINAL_REVIEW

Verdict: **PASS — 12 requested acceptance criteria, 0 failures.** Mezan Web is ready for the owner's final approval for the tested operational scope. No merge or deployment is authorized by this result. Android remains paused.

## Immutable tested candidate

- HEAD: `0d8e4147a398a0ddd3928c5e04a66adf67a66379`
- TREE: `55ca516f0daffc851337bb4f7e84b314261c94ef`
- Branch: `codex/operational-balance-system-20261005`; remote matched.
- PR: none (GitHub search for this repository/head returned no PR).
- Product source, tests, configuration and lockfiles were unchanged in this UAT phase. The subsequent evidence checkpoint changes documentation/evidence only.
- Previously accepted regression results: 149 Backend PASS and 43 Frontend PASS. These were not relabeled as fresh runs; this phase executed fresh Web/API UAT.

## Environment and method

Browser: `http://127.0.0.1:5178/operational-preview.html`.
Backend: `http://127.0.0.1:8135/api`.
Mongo: localhost27305; database `operational_balance_device_uat_20261005`.
New synthetic account: `web-final-uat-20261005`; no earlier preview account was reset.

The UAT host injects this synthetic principal into the unchanged API router. It uses the real source adapter, reconciliation, storage and worker with Mongo CAS. Browser interactions created the opening, finished initialization, saved the manual movement and displayed the resulting history/reports. A UAT driver seeded canonical MZ2 order snapshots and exercised actual operational refresh; it did not contact live Salla or test production authentication. API replay probes used the exact browser request payloads captured by the host.

The test host restricted the background worker to this UAT owner. Application database writes were intercepted and allowed only in operational collections. A runtime audit hook rejected accounting-module imports and non-loopback socket connections. Neither rejection was triggered. Fixture seeding itself used the same fixed local database, outside the product writer guard, and created only synthetic users/MZ2 accounts/binding/orders.

## Financial sequence

| Step | Observed balance | Movements | Result |
|---|---:|---:|---|
| Opening saved and finished | 10000.00 SAR | 0 | PASS |
| Manual outgoing250, no receipt | 9750.00 SAR | 1 | PASS |
| New bank-transfer order300, pending | 9750.00 SAR | 1 | PASS |
| Order becomes «تمت المراجعة» | 10050.00 SAR | 2 | PASS |
| Same order becomes «قيد التنفيذ» | 10050.00 SAR | 2 | PASS |
| Four concurrent resyncs | 10050.00 SAR | 2 | PASS |
| Conflict probes, reload, new tab, repeated refresh | 10050.00 SAR | 2 | PASS |

Expected independently: **10000 − 250 + 300 = 10050**. The supplier's250 advance appears separately; it was not added to bank liquidity.

## Acceptance matrix

| # | Requirement | Fresh observed evidence | Result |
|---|---|---|---|
| 1 | Opening not duplicated | Second UI entry for the same bank rejected with `operational_opening_duplicate`; count1. Four concurrent exact opening replays and four finish replays returned200 without changing financial hash. | PASS |
| 2 | Correct balance changes | UI/API displayed10000, then9750, then10050 according to the equation above. | PASS |
| 3 | Manual movement without receipt | No upload input; UI saved FINAL-UAT-MANUAL-250 and history displayed250. Payload/record receipt_id=null. | PASS |
| 4 | Bank transfer recorded once | Exactly one automatic incoming300 for UAT-FINAL-BANK-300. | PASS |
| 5 | Reviewed→in-progress does not repeat | Same canonical identity retained; count2 total movements and bank credit count1. | PASS |
| 6 | Resync does not repeat | Four concurrent real refresh calls plus running worker preserved financial hash. | PASS |
| 7 | Destination from MZ2 | Two active banks existed; confirmed `mz2_bank_transfer_bindings` maps `salla.payment_method_bank` / «مصرف UAT المرتبط» to `uat-bound-bank`. The recorded credit uses exactly that ID. `uat-unlinked-bank` received no movement. A second order without a binding created an incomplete-routing issue and no credit. | PASS |
| 8 | Reopen/readback consistent | Reload and independent new browser tab displayed the same two movements and10050. | PASS |
| 9 | Repeated refresh no duplicate | Two explicit history-refresh clicks retained two rows; final API read matched. | PASS |
| 10 | Errors/conflicts no partial money | Changed replay payload409, zero amount422, duplicate manual incoming409, changed source amount conflict, missing binding issue: persisted financial hashes stayed unchanged. Negative source fixtures restored or made ineligible for final readback; no rows were deleted. | PASS |
| 11 | No Accounting writer | Runtime accounting import attempts0; observed product write collections only `operational_balance_states_v1` and `operational_balance_operation_claims_v1`. Checked accounting collection counts for this owner0; no accounting source changes. | PASS |
| 12 | No Production writes | Fixed local backend/database; no production credentials/data used, no production endpoint called by this task. Runtime connection guard observed loopback destinations only. No merge/Prepare/Prepublish/deploy. | PASS |

Audit event counts at final read: baseline_saved1, system_started1, movement_saved1, order_bank_credited1. Final opening count1, total movements2, automatic bank credits1, issues0.

## Evidence

The `evidence/web-final-uat/` directory contains RESULT.json, stage snapshots (before/after hashes and responses), trace.jsonl, and the standalone UAT host/driver used outside product source. RESULT.json binds stage files by SHA256. Browser AX observations and a balance screenshot are also present in this conversation's tool record. No screenshot file is claimed in this evidence directory.

This is isolated operational Web acceptance, not production sign-in, live Salla sync, deployment, Android UAT, or whole-system final-review approval. The local preview remains on the final synthetic account for review.

**Production unchanged by this task. Production financial writes = 0.**
