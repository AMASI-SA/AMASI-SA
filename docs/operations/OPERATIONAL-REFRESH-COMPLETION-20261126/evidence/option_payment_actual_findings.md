# Actual option-authority and payment-view evidence

## Implemented boundary

`operational_balance_sources.py` now admits an explicit `OptionAuthorityScope`
only during `source_orders`. It builds a non-monetary reference observation of
the native catalog/binding containers once, lazily prepares successful catalog
and binding-label authority, and validates the observations before the complete
source result can return. The cache key includes the product and the exact
ordered binding row identities. Prices, quantities, selected bindings and
per-order failures are not cached.

The extracted catalog preparation, pre-cache per-item checks and post-cache
token resolution were independently compared to the previous source by AST;
their financial behavior is unchanged. Default `cost_components` callers do
not opt in. The source collector alone also opts into the separately reviewed
`normalize_operational_payment(..., read_only=True)` implementation. Its general
default remains the original independent-copy path.

Native binding records are read without a projection. They include Mongo
`_id: ObjectId` and native datetime metadata, which the first draft omitted from
its supported snapshot types. The final implementation supports ordinary native
metadata without conversion. It observes/rechecks ObjectId's replaceable byte
slot, supports exact naive or fixed-stdlib-timezone datetimes, and retains the
original lazy path for subclasses, custom time zones and unsupported objects.
Scope errors/cancellation close and clear the cache. A changed native authority
fails the entire source attempt outside per-order recovery.

No query, projection, row limit, financial formula, history date, four-reader
ceiling or request deadline was changed by this option-authority implementation.

## Focused regression evidence

The final fresh command was:

```bash
PYTHONPATH=backend:backend/tests \
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B \
  -m pytest --noconftest -p no:cacheprovider \
  backend/tests/test_operational_balance_option_authority.py \
  backend/tests/test_operational_balance_cost_catalog_index.py -q
```

**Exit 0: 176 passed in 3.75s, no skips.** This contains 63 new authority cases
and 113 existing cost-catalog cases. No MongoDB/service is needed for this
selection; its deny-list source fixture rejects unapproved reads/writes.

The cases cover full collector/reconciliation/replay parity, original validation
and Decimal flag behavior, currencies, duplicate/malformed catalog evidence,
translations, explicit-ID conflicts after warming, custom-object fallback,
successful-only preparation, per-attempt rebuilds, snapshot mutation rejection,
normal-exit validation, and owned build/validation cancellation with joined work.

Recorded red controls were deliberately observed before their changes:

| Control command suffix after the same pytest prefix | Exit / observed result |
| --- | --- |
| `backend/tests/test_operational_balance_option_authority.py -q` with its first regression only, before runtime implementation | Exit 1; 1 failed in 2.34s: eight catalog constructions for eight equivalent lines, expected one |
| `backend/tests/test_operational_balance_option_authority.py::test_native_binding_metadata_keeps_authority_ready_without_conversion -q` before metadata support | Exit 1; 1 failed in 1.14s: native ObjectId/datetime metadata made `scope.build()` return `None` |
| `backend/tests/test_operational_balance_option_authority.py::test_in_place_object_id_change_invalidates_the_native_snapshot backend/tests/test_operational_balance_option_authority.py::test_custom_datetime_timezone_keeps_original_lazy_path -q` before byte-slot/custom-zone guards | Exit 1; 2 failed in 1.25s: in-place ObjectId change was not rejected and custom timezone incorrectly entered the fast path |

An earlier two-case cancellation harness failure was a missing `nonlocal` on
the test's activation flag. That harness correction did not change runtime code;
the complete targeted selection was then rerun. The final result above includes
both cancellation cases and all later metadata guards. These are truthful local
command results, not a fabricated JUnit artifact. The existing strict CI glob
selects the new `test_operational_balance_option_authority.py` file for exact-A
verification.

## Final paired experiment

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONHASHSEED=0 \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B \
  docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/measure_option_payment_candidate.py \
  --output docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/measure_option_payment_candidate_result.json
```

**Exit 0.** One full A/B pair ran with the exact candidate runtime. Eight
untimed warm-up orders entered each path beforehand. Other heavy tests/timing
were held until this pair completed, and all owned tasks were joined.

The baseline disables only `OptionAuthorityScope.build` and forces the source
normalizer's `read_only=False`. The candidate uses the actual scoped authority
and actual guarded payment-view implementation. Both retain identical bundle
normalization and stdlib item copies, the same current `copy_report_value`
implementation, and the complete source predicates/queries/projections/limits.
The rejected small-item-copy prototype from the earlier profile is absent.

The cohort has 19,151 orders, 76,604 nested items, three options per item and 128
products. Every binding has deterministic ObjectId, naive created-at and UTC
updated-at metadata. The synthetic shares remain 25% catalog-name recovery,
50% cleared-provider payment, 25% partial provider payment and 25% COD. Actual
production shares are unknown. The source adapter validates all query shapes;
fake cursor cloning, fixture construction and hashing are outside timing.

### Observed timings

All values are instrumented wall milliseconds. Nested helpers/stages overlap
and **must not be summed**.

| Scope | Both opt-ins disabled | Actual option + payment opt-ins |
| --- | ---: | ---: |
| Complete source collector | 30,566.549 | 25,070.153 |
| `source_orders` | 14,216.590 | 10,576.560 |
| `_verified_option_tokens` | 3,202.092 | 1,133.941 |
| `catalog_options` builds | 19,152 | 128 |
| `catalog_options` time | 995.837 | 9.435 |
| Payment input copy, 9,576 calls | 1,938.521 | 649.026 |
| Public snapshot / actual read-view, 14,364 calls | 2,034.646 | 734.559 |
| Authority snapshot build | disabled | 19.770 |
| Final authority validation | disabled | 8.976 |
| Unchanged bundle normalization | 13,646.373 | 11,830.304 |

The targeted `source_orders` stage fell by **3,640.030 ms (about 25.6%)** in this
pair. The deterministic work reduction is catalog construction from 19,152
instances to 128 and replacing 9,576 whole-order payment copies with the actual
guarded financial-input copies. The candidate explicitly asserted that all 128
product authorities were cached, so native metadata did not silently disable
the optimization.

The complete collector difference is 5,496.396 ms, but **do not attribute that
whole difference to these changes**. Unchanged bundle normalization also varied
by 1,816.069 ms between runs. GC attribution and run-order variability remain;
the measured overall GC time was 4,443.178 versus 4,880.988 ms. This single warmed
pair is evidence of reduced work on this fixture, not a stable live-time SLA.

### Representation and preservation

The authority observes 5,250 native containers/ObjectId byte slots. Its auxiliary
representation measured **1,424,776 bytes** after snapshot construction and
**2,310,654 bytes** including 128 prepared catalogs. This untimed `sys.getsizeof`
graph accounting excludes the entire borrowed source graph, including native
ObjectId byte buffers. It is not a process RSS or production peak-memory claim.
The cache is bounded by the authority used in this attempt and is cleared on
scope exit; this trades a small retained catalog representation for repeated
construction/allocation. Complete-process high-water RSS in the JSON is
cumulative and also retains the baseline result for direct equality.

Both paths returned all 19,151 orders and 76,604 items. Direct equality and all
recorded preservation assertions passed:

- Complete output pickle/type/order/alias digest:
  `23e16abfbe0855486632c08b70d2dc71008febd99c30c55c88282104949a6339`.
- Collection query sequence digest:
  `ada563f25ef95250e3c6ea92a65c71054d149d68256f79fd1f7a872a28071cf1`.
- Full synthetic input digest, now including native metadata:
  `c08140d7a748e979d6cb11c406ee43120c662ae5f576fbd2dce8b577e521c24e`.
- Operational input state unchanged; all ten recorded runtime source hashes
  unchanged before/after the pair.

There was no database/network/production access, financial write, refresh retry,
new endpoint or source truncation. The fixture has empty physical bundle,
supplier and ledger history and no concurrent profit/native-proof preparation.
It does not prove those live financial records or completion of the whole
production 85-second workflow. The laboratory's 85-second cap bounds each
offline source run only. Exact-source CI and one authorized live acceptance
attempt remain separate requirements owned by the release coordinator.

## Frozen SHA-256 identities

| File | SHA-256 |
| --- | --- |
| `backend/operational_option_authority.py` | `41cfcf5720ff1a08069b7502f3de24198ecedc7adc25862555b66ae5680e144d` |
| `backend/operational_balance_sources.py` | `f8f8046218005e485bea1985dd4ad2b99c3dfc53f2a4f518c8aad981ef158200` |
| `backend/tests/test_operational_balance_option_authority.py` | `f8b9441a3c2116a72de419e649dc84af7b33af9957108a73f0a5155b5c209a3d` |
| `evidence/profile_source_preparation.py` (original reusable synthetic fixture) | `402c2a4df247373da7c06da004c81a64313cbb8e060996c62f863c9991dfc19b` |
| `evidence/measure_option_payment_candidate.py` | `d04b352bd4c2ca037cac91d0a8e9ddbf92cb59ab2db2bd3dece6644e6a7d8233` |
| `evidence/measure_option_payment_candidate_result.json` | `20f0f453990b07cde688fc70cc29e50213e59aa7fd17e4409eb75d5c2dda580f` |

The JSON contains the full hashes for the other shared runtime collaborators,
including the unchanged-in-both copier lifetime fix. No additional timing or
optional test expansion is needed for this handoff.
