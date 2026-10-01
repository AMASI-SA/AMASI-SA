# Existing writer wiring follow-up — RELEASE BLOCKED

The latest instruction authorizes completion of delivered V2 wiring only. It does not authorize a new domain writer, changed accounting economics, Legacy fallback, historical migration or write-control changes. Work started from `a53eb99178e92fe5d1d698fe8bf7e5a150239c6e`. The frozen-source audit is [DELIVERED-WRITER-PROVENANCE.md](DELIVERED-WRITER-PROVENANCE.md); earlier verification remains historical evidence, not a claim for this follow-up HEAD.

## Existing paths and scope boundaries

| Flow | Existing native path / repair | Remaining limitation |
|---|---|---|
| Salla delivered COD / external shipping | Canonical source snapshot → `observe_delivery` → `recognize_cod` / `recognize_fee_delivery` → native `_post` → `post_journal_v2`. Already registered. | COD is an existing native sale producer; it cannot be reused as a generic non-COD/provider sale or refund writer. |
| Driver delivery | Removed a premature return that made the delivered `observe_driver_delivery` hook unreachable. It now executes after operational evidence is bound. | Operational delivery success is retained if accounting is paused or observer fails; it does not approve payment. |
| Driver bank/POS review | Existing `review_driver_payment` and `settle_pos_to_bank` reach native `_post`. Existing Track A bank identity resolver is used by bank selectors and transfer submission/resubmission. | Production verified-destination adapter still returns503; canonical account selection is not proof of bank arrival/POS success. Tests substituting that adapter verify the consumer only. |
| Advertising spend | Delivered `post_spend` and automation `_book` already call native `post_journal_v2`. Replaced the local bank-identity stub with Track A `require_financial_ledger_identity` inside the same owner transaction. H2 now names the remaining evidence/posting gap. | Bank funding/payable payment remain409 `track_a_bank_evidence_and_posting_integration_required`; no funding writer or evidence-consumption adapter is created. |
| Supplier invoice/payment | Native invoice close and payment/allocations routes are registered. `post_native_invoice`, `accounting_supplier_payments_v2.settle`, purchase receiving and supplier payment services use native journals. Earlier restored canonical supplier guard and real Track A payment port are preserved. | No additional dropped supplier hook was found. Supplier Native CI also includes a shared customer-refund regression that fails at its initial non-COD sale. |
| Employee finance | Canonical employee/salary identity and registered routes exist. | Financial accrual/classification still calls `ledger_core.post_txn_group`. No delivered native payroll producer was found; not repaired by replacing an identity lookup. |

COD collection and shipping/delivery fees remain independent. No netting, repeated revenue, inferred identity, new financial writer or fallback was introduced. `read_verified_journal_metadata_v2`, opening gates, period checks, native idempotency and owner transactions are preserved.

## Executed verification

All writes below are to unique disposable synthetic Mongo8.0.12 databases on loopback27128/27129. Python3.13 is local; CI uses its pinned runtime. No production or preview service was contacted.

| Check | Before | After / observed result |
|---|---|---|
| Driver actual operational-delivery HTTP route |3 failures: observer never called despite200 response |3 passes, covering native posting, pause and observer error; affected driver batch66 passed |
| Driver/accountant bank selectors and delivery transfer FK |10 failures, including observed Legacy account reads |10 passes in14.42s; canonical-only bank accepted,9 invalid identity cases rejected; payment remains pending and actual approval stays503 without verified-destination adapter |
| Driver rejected-payment resubmission bank FK |10 failures, including observed Legacy account reads |10 passes in12.91s; only canonical bank resubmits; no journal, shipping event or bank-movement consumption; invalid request retains rejected review and uploaded receipt |
| Advertising actual Track A bank identity |9 failures against the unimplemented stub |9 passes: canonical bank reaches only remaining evidence barrier; missing/foreign/inactive/archived/nonbank/nonSAR identities fail closed; transaction revision and financial collections unchanged |
| Full advertising workflow selection |Not rerun against old source as a whole |253 passed in138.21s, zero failures/skips; includes ledger, onboarding and platform-reporting regressions |
| H2 advertising UI |Reason changed to reflect actual remaining blocker |7 passed in5.152s |
| Full affected driver/shipping/identity selection |Focused failures above |215 passed and6 subtests passed in140.20s; five existing Pydantic warnings; all `test_store_delivery*.py` plus native driver/shipping/bank/P02 and identity suites |
| Native reports and onboarding domains |Existing contracts retained |31 passed in3.07s |
| Existing sales/refund/settlement acceptance cases |Previously fail at initial sale |Fresh rerun:3 failed in4.16s,423 `accounting_legacy_writer_disabled`; downstream assertions unreached |
| Payroll native blocker probe |Previously source/guard evidence only |Verified native opening, safe active/readiness available; actual accrual, salary payment, advance grant and custody grant services each return423 `accounting_legacy_writer_disabled`; all persisted documents unchanged after every attempt |

The payroll probe's exit0 means its blocker assertions succeeded, **not payroll business acceptance**. It uses real services and XLSX daily-movement import, no mocked writer/gate, no historical activation, and removes its generated synthetic database in `finally`. Runner: [payroll_blocker_probe.py](payroll_blocker_probe.py). Reproduce from repository root:

```powershell
$env:PYTHONPATH='backend;backend/tests'
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27128/?replicaSet=mz2test'
.venv/Scripts/python.exe docs/operations/MZ2-FINAL-INTEGRATION-20261001/payroll_blocker_probe.py
```

Full follow-up CI and final source identity are recorded in the checkpoint addendum when completed. Earlier `CI-MATRIX.json` describes its explicitly named older source. Complete frontend and business UAT remain NOT_PASS as recorded in [FRONTEND-REGRESSION.md](FRONTEND-REGRESSION.md) and [MZ2_ONLY_SSOT_AUDIT.md](MZ2_ONLY_SSOT_AUDIT.md); focused green tests do not replace those gates.

Production writes=0; production merge=NO; deploy=NO; opening post=NO; activation=NO; write-control unchanged. PR1222 remains draft. Real missing producer/evidence contracts need new authorized scope or delivery; they are not bypassed in Final Integration.
