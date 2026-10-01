# C3: Native physical-cash reconciliation — independent contract audit

Audited repository: AMASI-SA/AMASI-SA.
Worktree: `C:/Users/amasi/mz2-c-rich-shipping-20261001`.
Source HEAD for the reproduction: `cc84b44bfb983a1a717ba1bd50074df64d193bc0`.
Frozen integration checkpoint remains separately preserved at
`9e884f697c03e52bb98513f1cea141b0d31261aa`.

This review introduces no repository changes, financial writer, new custody
attestation, opening balance, production mutation or deployment. The bounded
reproduction below uses disposable databases on `127.0.0.1:27130`, replica set
`mz2c`; synthetic Opening and journals exist only inside those fixtures.

## Verdict and scope

A truthful post-cutover reconciliation reader is implementable from existing
operational and verified Native sources. A complete current physical-cash
balance cannot be proved solely from the delivered data. Opening physical
custody and operational-remittance-to-Native linkage are missing factual
bindings; a read adapter cannot infer either. Their absence does not imply a
need for new journal economics, but any future authenticated source/attestation
intake must be identified explicitly as new source-contract work.

A separate chronology defect in the existing cash settlement writer was
reproduced on real Mongo. It is an existing timing/cash-separation contract
violation, not a reason to invent new accounting behavior. No fix was applied
by this audit.

## Source contracts and exact anchors

All source paths below are relative to the audited worktree and HEAD.

| Source | Existing authority | Boundary |
| --- | --- | --- |
| `backend/store_delivery_domain.py:101` (`collection_requirements`) | Cash explicitly sets `cod_custody_amount` equal to outstanding at line129. Noncash sets custody zero at line138 while retaining outstanding responsibility. | COD responsibility cannot substitute for physical cash. |
| `backend/store_delivery_driver_app_routes.py:1880` (collection row) | Owner, driver, collection ID, assignment/order identities, amount, payment method, custody amount, bound proof reference and collected time. | Operational-only input, not Native balance authority. |
| `backend/accounting_shipping_native_evidence.py:130` (`driver_facts`) | Exact owner/driver/assignment/collection and bound delivery proof. Cash custody equality checked at160; exact collection identity and hash enter sealed Native evidence at176–179. | This function pins sources and must not be invoked by a read-only adapter. It does not establish an independent physical Opening count. |
| `backend/accounting_shipping_native.py:43` (`_rows`) | Requires V2, verifies MZ2 ledger and group metadata. | Use this boundary rather than direct unverified physical ledger reads or Legacy fallback. |
| `backend/accounting_shipping_native.py:265` (`settle`) | Native cash receipt uses owner/party, movement ID, binding and actual journal. Event metadata at332; consumed movement links event and journal at338. | No operational settlement ID; no per-collection allocation. See chronology finding below. |
| `backend/accounting_driver_payment_review.py:137` | Noncash approvals use `action=receive_cod` with `settlement_origin=driver_payment_review`; event kind at195. | These and POS-bank settlements must be excluded from physical-cash remittances. |
| `backend/store_delivery_settlement_routes.py:74` (`_totals`) | Operational collection and settlement summary. | Do not reuse as Native reconciliation: it clamps negatives, includes legacy operational net-settlement behavior and has no Native verification. |
| `backend/store_delivery_settlement_routes.py:219` (operational settlement row) | Remittance ID, exact driver, amount, type, timestamp, free-text reference at228. `posting_scope=operational_balance`/`accounting_status=operational_only` at230–231. | Explicit `ledger_txn_group_id=None` at234. Free-text reference, date or amount does not establish Native linkage. |
| `backend/accounting_financial_accounts.py:207` (`OpeningLine`) | Typed financial facts; driver categories at211, amount, evidence, currency and meaning. | Only driver COD receivable and fee payable are present. There is no driver physical-cash/noncash split or custody-count field. |
| `backend/accounting_financial_identity.py:52` | Generic cash account maps to `bank/<canonical-account-id>/main`. | It is not driver physical custody; employee custody is also a different contract. |
| `frontend/src/pages/accounting/h2/driverAdapter.js:7` and `DriverPanel.jsx:45` | Explicit missing physical-cash read capability. | Do not replace the placeholder with a derived total COD balance or operational summary disguised as Native. |

## Minimum safe read adapter

Suggested new read result shape, derived only from existing fields:

```json
{
  "party_type": "store_driver",
  "party_id": "exact-driver-id",
  "ledger_source": "accounting_v2",
  "scope": "post_cutover",
  "cutover_at": "verified-cutover-time",
  "native_cod_receivable": "verified-native-total",
  "native_delivery_fee_payable": "separate-verified-native-total",
  "sealed_cash_collections": [],
  "verified_native_cash_remittances": [],
  "operational_remittances_without_native_link": [],
  "post_cutover_cash_responsibility_delta": "verified-cash-movement-delta",
  "opening_physical_cash": null,
  "current_physical_cash": null,
  "reconciliation_status": "not_ready",
  "reasons": ["opening_physical_cash_source_missing"]
}
```

This is a proposed interface, not a delivered endpoint or a PASS result.

Required checks:

1. Fresh accounting view permission, exact owner/driver identity and V2-only
   report boundary. Reads must work while writes are paused without invoking
   `atomic_owner`, source pins or any mutation.
2. Validate each Native evidence seal, canonical source identity and exact
   owner/driver/assignment/order/collection link. Reject duplicates, corrupt
   evidence and contradictory current source facts. Bound every result set and
   fail on truncation instead of claiming complete reconciliation.
3. Count a Native cash receipt only if event, actual verified group/legs and
   consumed movement agree on owner, driver, amount, event ID, movement ID,
   journal ID and bank debit/driver COD credit.
4. Exclude bank/POS review settlements, POS-bank settlements and every fee
   accrual/payment. Do not net fees against cash.
5. Return unlinked operational remittances separately. Never match them by
   similar amount/date, free-text reference, generic account name or heuristic.
6. Missing physical Opening cash is null/not-ready, not zero. The post-cutover
   delta is not a certified physical balance. Reversals, unallocated corrections
   and inconsistent chronology remain explicit reconciliation exceptions.
7. Use Decimal amounts and actual timestamps, not float totals or clamping
   negative results to zero. H2 must display backend totals and truthful scope,
   never derive a balance from a paginated table.

## Missing factual bindings

**Opening physical custody:** an authenticated, dated, owner/driver-specific
cash observation or custody allocation with retained evidence and an explicit
relationship to the cutover. Existing total driver COD Opening facts, including
an explicitly zero financial fact, are not a separate physical observation.

**Operational remittance link:** a verifiable source identity linking a specific
operational remittance to the actual Native movement, event and journal.
Current operational rows deliberately leave journal/operation linkage absent.
An unlinked operational outflow must not be subtracted again from a Native
receipt, nor silently treated as the same receipt.

## Confirmed finding: earlier noncash responsibility authorizes backdated cash

Severity: high financial correctness impact; confidence: high, directly
reproduced. Classification: existing integration/timing defect within the C3
consumer scope; no new economics is needed to preserve the existing constraint.

The existing writer comment requires cash-only handovers at
`accounting_shipping_native.py:298`, and disallows a later recognized balance
authorizing a backdated settlement at325. Implementation selects cash evidence
without a date cutoff at301–307, subtracts undated cash receipts at308–311,
then at327 checks dated **total** driver COD, which includes earlier noncash.

Independent scenario:

1. Actual existing driver recognition creates SAR500 bank-transfer responsibility
   on 2026-09-03T12:00Z. Its review remains pending and physical custody is zero.
2. Another actual driver recognition creates SAR500 cash responsibility from a
   delivered/collected assignment dated 2026-09-05T12:00Z.
3. A real preserved XLSX bank import supplies SAR200 incoming movement dated
   2026-09-04. The existing canonical Track A resolver and confirmed binding are
   used; no resolver, evidence authority or journal writer is mocked.
4. Existing `settle(...action=receive_cod...)` incorrectly succeeds. It posts
   Dr Bank200 / Cr Driver COD200 at **2026-09-03T21:00:00Z** (Riyadh start of the
   bank movement date), before the supporting cash collection; marks the bank
   movement `accounting_posted`; creates one Native settlement event; and changes
   driver total COD from1000 to800.
5. Control with the identical later cash collection and bank movement but no
   earlier noncash responsibility correctly rejects409
   `shipping_settlement_before_liability`, with no journal/movement mutation.

Expected: both cases reject because the supporting cash was collected later.
The earlier noncash balance must not satisfy the dated cash-availability bound.
Smallest repair direction: apply the existing movement-time cutoff to eligible
cash evidence and its verified allocation/receipts as well as the total COD
guard; preserve the full-lifecycle no-overreceive guard and existing no-netting
economics. Root owns classification and any subsequent implementation.

Fresh execution:

```text
python -m pytest -q -p no:cacheprovider test_c3_chronology_probe.py
1 failed, 1 passed in 3.21s; exit1
```

The failed assertion is the required no-posting behavior, not a weakened
expected-bug success test. Artifacts in this directory:

- `test_c3_chronology_probe.py` — external probe, no repository edits.
- `c3-chronology-probe.xml` — fresh expected regression failure.
- `c3-chronology-with-earlier-noncash.json` — actual incorrect journal and effects.
- `c3-chronology-without-earlier-noncash.json` — correctly rejected control.
- `c3-chronology-cleanup.json` — independent fixture cleanup verification.

Execution environment: Python owner virtual environment, Motor3.7.1,
Mongo8.0.12 loopback replica set `mz2c`; `PYTHON_DOTENV_DISABLED=1`; Mongo env
variables pointed only to the loopback fixture. Existing unique Track F fixture
created and removed its own databases and retained the NoLegacy command monitor.
No environment values or credentials from Production were read or printed.

## Required eventual acceptance tests

- Cash500 + POS500 + bank-transfer500 => responsibility1500, cash collection500.
- Native cash receipt200 => post-cutover cash delta300; fees unchanged.
- POS approval and later POS-bank settlement never alter cash custody totals.
- Unlinked operational remittance remains unresolved and is not counted twice.
- Missing/nonzero/zero financial Opening does not fabricate physical Opening.
- Exact scope, duplicate source, broken seal, inconsistent journal/movement,
  reversal and chronology cases fail closed without partial mutation.
- Paused reads are read-only, Legacy monitor stays empty, and UI preserves null
  and incomplete evidence states instead of asserting physical cash PASS.

Production financial writes =0. Merge=NO. Deploy=NO. Opening Post=NO Production
action. Activation=NO Production action. Write-control unchanged in Production.

## Authorized chronology regression repair — 2026-10-01

Root authorized only `accounting_shipping_native.py:settle()` and a dedicated
new Real Mongo regression suite. This repairs the existing cash-only/date guard;
it does not supply the missing physical-cash Opening or operational-remittance
link contracts described above and does not close C3 physical reconciliation.

- Source baseline: `f3be643522d8d3709a0508a9d3aba96fdbab727a`.
- Baseline new suite: **7 FAIL / 4 PASS**, `13.31s`; all failures incorrectly
  accepted backdated cash receipts. Evidence: `c3-chronology-regression-before.xml`.
- Focused result after repair: **11 PASS**, `12.74s`.
  Evidence: `c3-chronology-regression-after.xml`.
- Adjacent Native Shipping and Driver payment-review regression: **95 PASS**,
  `67.71s`; evidence: `c3-chronology-adjacent-regression.xml`.
- Independent loopback inspection after the focused run found no remaining
  database with the `mz2_driver_cash_chronology_` fixture prefix.
- Runtime: movement date validation now precedes cash availability. Supporting
  sealed cash delivery and order-linked journal rows must both precede/equal the
  existing Riyadh midnight cutoff, and generic cash receipts through the same
  cutoff are deducted. Retains the full-life cash bound, total COD bounds,
  noncash-review separation, original 409 code, 423 and actual owner transaction.
- Tests cover earlier pending bank/POS and opening COD, a no-prior-liability
  control, prior dated receipts, valid partial/full cash, fee separation,
  idempotency, Riyadh midnight, paused 423, and actual journal fault rollback.
  Every success/failure runs the NoLegacy monitor; rejected actions compare all
  fixture documents for zero mutation.
- Changed files are only `backend/accounting_shipping_native.py` within
  `settle()` and `backend/tests/test_mz2_driver_cash_chronology.py`.
- No commit or push by this agent. All fixtures are disposable loopback Mongo
  replica-set databases; Production financial writes remain zero.
