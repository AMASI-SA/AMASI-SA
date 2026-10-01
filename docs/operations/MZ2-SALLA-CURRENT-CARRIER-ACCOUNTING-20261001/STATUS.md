# MZ2_SALLA_CURRENT_CARRIER — separate Accounting Integration

State: implementation complete and verified for review in Draft [PR #1234](https://github.com/AMASI-SA/AMASI-SA/pull/1234). No merge/deploy/Production writes. Operational PR #1231 stays unchanged and operational-only. The financial exposure is not fixed in Production.

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
- [CI run 36905106575](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36905106575) PASS: **543 tests +549 subtests**, 27 native/driver/ledger/atomic/operational suites, zero skipped/failed/error cases. Both applicable checks passed: `Existing V2 writer — A-I and unchanged history` and Qoyod `backend-unit`.
- Tested implementation HEAD: `570719aa6dd1451c7625752349ec260715f3505a`; TREE: `8c282e107bf06120a0b57e5eabe11b98b8511c14`. This report is a documentation-only checkpoint; final immutable HEAD/TREE and final-head CI are recorded in PR #1234 and canonical [Issue #1006](https://github.com/AMASI-SA/AMASI-SA/issues/1006).
- [Artifact 11184335179](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36905106575/artifacts/11184335179) contains `source-proof.json`, `baseline-regression.xml`, `acceptance.xml`. ZIP SHA256 verified locally: `980ba8f107043b318c81ddaf0fffcabb4624bac9fe2aff855af39f45f8448ea1`. Parsed XML confirms the intended unchanged-writer baseline assertion failure and all 543 passing candidate cases.
- Operational dependency pinned: HEAD `6507c813a73ea71a5eab2a8d39c5bb92a4075c70`, TREE `cb9507cbf79ce401a3381c3311355c0cd3effe05`. Its 19 checks passed (4 unrelated skips); genuine changed-carrier provider webhook remains unobserved.

## Exact changed files relative to latest #1232

| File | Bounded purpose |
| --- | --- |
| `backend/accounting_shipping_current_guard.py` | New read-only current carrier/shipment adapter; no DB writes, provider calls, posting or rate calculation. |
| `backend/accounting_shipping_native.py` | Guard new courier fee after posted replay, before existing rate/posting. |
| `backend/accounting_shipping_native_evidence.py` | Optional binding on newly sealed external facts; canonical COD/driver fact functions unchanged. |
| `backend/accounting_shipping_p02.py` | Guard new imported eligibility; old writer and posted replay unchanged. |
| `backend/accounting_public_errors.py` | Fixed safe public guard error codes. |
| `backend/tests/test_mz2_shipping_current_carrier.py` | 37 adapter/imported-reader cases. |
| `backend/tests/test_mz2_shipping_current_carrier_native.py` | 15 real writer/transaction A–I cases. |
| `backend/tests/test_mz2_shipping_native.py` | Add current shipment facts to synthetic fixtures; original assertions unchanged. |
| `scripts/verify_current_carrier_fee.py` | Immutable source/scope proof, actual baseline reproduction and zero-skip acceptance. |
| `.github/workflows/mz2-current-carrier-fee.yml` | Disposable loopback Mongo and pinned checkouts, read-only GitHub permissions. |
| `docs/operations/MZ2-SALLA-CURRENT-CARRIER-ACCOUNTING-20261001/STATUS.md` | This handoff report. |

Every other tracked file matches the newest integration base. Existing ledger/journal primitives, fee calculation, tariffs/setup, write-control, cutover, COD/revenue/Driver Receivable and settlement implementations are unchanged. CI additionally compares ASTs for `_post`, `_economic`, `recognition_legs`, `_seal_delivery`, `settle`, `canonical_facts`, `driver_facts`, `select_courier_rate` and `post_courier_fee`. Native fixture command monitoring confirms the native writer does not access retired `general_ledger`, `accounts`, `counterparties` or `shipping_company_settings`.

## A–I acceptance proof

| Case | Executed passing result |
| --- | --- |
| A | Current valid iMile proof posts one iMile fee through existing V2 writer and approved rate. |
| B | iMile → Store Driver with old sealed iMile evidence rejects; financial snapshot unchanged; zero new courier fee. |
| C | Old imported iMile evidence rejects for current SMSA. Missing current proof rejects. Valid current SMSA proof posts only SMSA; old import unchanged. |
| D | Archived/cancelled/return/superseded/replaced shipments reject. Inactive imported flags and reused AWB ambiguity also reject. |
| E | Newer Store Driver intake followed by stale iMile event retains Store Driver. Fee racing operational carrier transaction waits, re-reads, then rejects old evidence. |
| F | Posted fee replay after carrier change returns the same journal; complete financial snapshot unchanged. |
| G | Missing metadata/ambiguous order rejects with no new fee; unknown/contradictory carrier mappings and missing binding also reject. |
| H | Store Driver creates no Courier Company Fee event/journal. Existing driver accounting regression suite passes unchanged. |
| I | Five concurrent retries produce one fee event/journal and two fee legs; four immutable replays. |

The actual unchanged baseline V2 writer fails case B specifically with `DID NOT RAISE HTTPException`, because it still creates the old iMile fee after the current carrier becomes Store Driver. The same case passes under the guard and asserts byte-equivalent financial collection snapshots. This is direct executed proof that stale carrier evidence cannot create a new fee through the guarded writer.

The CI operational composition applies only #1231's delta in a temporary validation checkout and verifies existing `financial_delivery_snapshot` and `pin_financial_delivery_snapshot` unchanged. No branch is merged or deployed. The earlier failed full-file-copy harness run is not acceptance evidence.

## Limits and next safe action

Ready for separate review only. Unknown/incomplete current proof intentionally blocks new fees; no automatic historical correction, reclassification, backfill or unposted evidence repair. Existing sealer does not automatically replace old incomplete native evidence. Integration must preserve #1231's operational delta together with this guard and retain combined acceptance proof. Final Integration's independent release blockers remain outside this task.

A genuine Salla changed-carrier webhook remains unobserved; conditional real-provider testing is not claimed passed. No shipping scope request or provider mutation added. Operational #1231 has zero accounting/ledger/rate/control source changes, preserved HEAD/TREE, 19 successful CI checks, 4 unrelated skips, local257 backend+561 subtests and28 frontend PASS, and real isolated Mongo intake/concurrency/standalone rollback PASS.

Next safe action: review #1234 against pinned #1232 and #1231 dependency using the source/A–I artifacts; inspect final-head CI. Obtain a genuine changed-carrier provider event only if available without Production mutations. Any eventual merge/deploy requires separate user authorization.

Production changed: no. Production financial writes: 0. No merge, deploy, release lease, cutover, write-control, tariff or historical journal change.
Production branch remains `901568ccaaf510dc1f84d9c28f38d368d07dc64d`.
