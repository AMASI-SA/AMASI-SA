# Source preparation profile, 2026-11-26

This is **synthetic offline evidence**, not a production trace or a successful
refresh. Runtime source was unchanged throughout the experiment. No database,
network, financial writer, application endpoint or production configuration was
used. The existing deny-list source adapter validated the query shapes; its
cursor returned prebuilt rows so fixture cloning was not charged to production
preparation work.

## Command and result

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONHASHSEED=0 \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B \
  docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/profile_source_preparation.py \
  --output docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/profile_source_preparation_result.json
```

Exit status: **0**. One baseline and one experiment-only candidate completed.
There was no retry. Heavy timing was coordinated with the other reviewer so
their benchmark was stopped during these two measurements.

The fixed cohort contained 19,151 orders, 76,604 nested items, three selections
per item and 128 product identities. The order payloads encoded to 92,268,810
BSON bytes. One quarter of orders used product catalog name recovery, one half
had the explicit provider collection-clear branch, one quarter had a partial
provider collection and one quarter used COD. These distributions are declared
fixture assumptions; their frequencies in production are unknown.

Both runs returned all 19,151 orders and 76,604 items, with no source issues in
this fixture. The following checks passed:

- Direct equality of the complete source result.
- Equal serialized output, including types, order and aliases:
  `23e16abfbe0855486632c08b70d2dc71008febd99c30c55c88282104949a6339`.
- Equal collection query sequence:
  `ada563f25ef95250e3c6ea92a65c71054d149d68256f79fd1f7a872a28071cf1`.
- Unchanged complete input fingerprint:
  `6f8e0dc3b6c312d4c300365ab8244fd03bf9bc7711331ce939646a5b7fd45fc4`.
- Unchanged operational state and the seven runtime source hashes recorded in
  the JSON result.

## Measurements

Times below are instrumented wall milliseconds. Helpers and stages are
inclusive and overlap: **do not sum them**.

| Scope | Baseline | Combined experimental candidate | Interpretation |
| --- | ---: | ---: | --- |
| Complete source collector | 29,991.951 | 24,731.030 | 5,260.921 ms lower in this single ordered pair |
| `source_orders` | 13,667.041 | 9,849.072 | 3,817.969 ms lower |
| `_verified_option_tokens` | 3,020.922 | 1,116.355 | Reuses product authority; per-line checks remain |
| `catalog_options` | 951.631 | 6.447 | Builds fall from 19,152 to 128 |
| `settled_payment_snapshot` | 1,947.579 | 114.208 | Same eligibility and scalar update logic |
| Its payload copy, 9,576 calls | 1,857.974 | 33.512 | Benchmark-only three-container overlay |
| Bundle item copy, 76,604 calls | 5,565.038 | 7,515.054 | Existing copy helper substitution regressed |
| Complete bundle normalization | 13,319.481 | 12,088.739 | GC attribution moved between nested helpers; not proof of a copy improvement |
| Total observed GC time | 4,295.337 | 4,892.537 | Increased under the per-item helper experiment |

The prototype option cache contains 128 product entries. It reuses validated
catalog/binding authority, never an amount or per-order decision. The complete
original per-line option identity and conflict checks still run. A production
implementation additionally needs an explicit immutable attempt scope and
fallback for unsupported/mutated source shapes; this script is not such an API.

The payment prototype changes only the copier called by the existing helper,
leaving its eligibility and field-update logic intact. It borrows unrelated
subtrees only for this read-only laboratory collector. The public helper's
independent-copy behavior must remain intact. Any production optimization needs
guarded alias/subclass fallback and direct normalizer no-mutation/output tests.

## Rejected bundle-copy substitution

Replacing each small item `deepcopy` with `copy_report_value` is not supported by
this experiment. Its own measured time increased, and the candidate GC freed
hundreds of thousands of additional objects. The helper's recursive inner
closure and per-call memo can create cyclic garbage when invoked once for each
small item; that mechanism needs its own proof before any such application.
The already reviewed single large engine-state clone has a separate measured
benefit and was not changed here.

The candidate process high-water RSS reached 1,042,508 KiB after the baseline's
941,832 KiB. Both measurements share a process and the baseline result was kept
for direct equality. These are **not independent peak-memory comparisons** and
must not be used to claim a production memory improvement or regression.

## Claim limits and next decision

This pair supports prioritizing request-local product option authority reuse
and a narrowly guarded read-only payment overlay. It does not prove that either
alone resolves the live timeout, and its combined timing includes the rejected
bundle-copy experiment, import warming and GC distribution. The deterministic
reduction from 19,152 catalog constructions to 128 and removal of 9,576 complete
payload copies are stronger evidence than treating one timing pair as a stable
performance guarantee.

The fixture contains no populated frozen bundle authority, supplier history,
existing ledger, bank approvals or driver assignments. It therefore does not
verify those financial contracts. It also has no BSON transport, database I/O,
concurrent profit work or native-proof preparation. Its 85-second cap only
bounds this offline source run; it does not model the production request's
single 85-second end-to-end deadline.

No runtime source change has been made by this experiment. The next safe action
is a scoped implementation decision with the existing malformed-evidence,
currency, duplicate, original-date, replay and cancellation regression gates.
