# Report-only copy dispatch optimization

## Measured reason and deliberately retained isolation

Base: `8bc807e24f2258d2ccc95c63292768f451bc3983`.

The existing dense pure fixture combines 4,000 provider orders, 5,000 COD
claims and 10,000 movements. An initial unprofiled providers build took
1,509 ms; its four service copy calls took 385 ms: 252 ms for the full state,
113 ms for active obligations and 20 ms for COD metrics. A separate cProfile
run confirmed repeated generic scalar/container copy dispatch as a material
CPU cost. These are local synthetic observations, not production timings.

`report()` first copies the saved state before `reconcile_credits()` derives
supplier/provider credits. It separately copies active obligations before
display enrichment, while later fee and foreign-currency projections still
need the native original claims. Removing either protective copy would change
that isolation contract. This patch retains both and every other existing
report copy.

The seven callsites inside `report()` now use `copy_report_value`. No write,
refresh, reconciliation, archived snapshot, mutation or CAS copy call changes.
The helper belongs to one report copy operation; it does not cache financial
results or retain values between calls.

## Copy protocol

Only exact built-in dictionaries and lists use the specialized container
traversal. Exact `str`, `int`, `float`, `bool`, `bytes` and `None` retain their
original immutable identity without repeated generic dispatch. All subclasses,
BSON values, dates, decimals, tuples, sets and custom copy hooks use the
standard library's `deepcopy` with the same memo.

The memo is installed before traversing a mutable container, preserving aliases
and cycles across both paths. A fallback hook can replace even atomic values
in that memo; subsequent atomic keys and values honor those replacements.
After a container finishes, its memo entry is restored to its actual result,
as the standard library does even when a child hook temporarily changes it.
Original containers remain alive through the same standard-library keepalive
protocol. The helper also checks cooperative cancellation at entry, exit and
every 256 non-atomic visits; a cancelled caller receives no partial copy.

## Verification

The final author gate passed **242 tests in 13.99 seconds**, with no skips.
The new module contributes 55 cases. Tests compare all twelve report views
byte-for-byte with the standard-copy path on existing rich, scoped and
repeated-profile fixtures and on a new synthetic 2030 credit snapshot.

The credit case independently checks supplier overpayment credit 50.00 with
25.00 remaining, provider excess credit 20.00 with 15.00 remaining, and the
bank movement total. It verifies that changing nested returned proof and
obligation values cannot change the input. Other cases cover all original
inputs remaining unchanged, typed BSON and Python values, non-string keys,
shared aliases, cycles crossing fallback tuples, subclass hooks, custom memo
replacement, keepalive, copy errors and cooperative cancellation.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  PYTHONPATH=backend:backend/tests \
  /workspace/scratch/56d90f85c1cc/test-venv/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_operational_balance_report_copy.py \
  backend/tests/test_operational_provider_report_profile_memo.py \
  backend/tests/test_operational_balance_statement_fees.py \
  backend/tests/test_operational_balance_configured_provider_fees.py \
  backend/tests/test_operational_financial_position_views.py \
  backend/tests/test_operational_balance_cod_projection_performance.py
```

Frozen SHA-256 values:

- Copy helper: `e506402af03127a2679a30940523dfd76644903a5c637e8e1080b45c63d850ac`
- Service: `b9aad2715e3af1cf5dfee3c076aff0b62bde0b3fa01d286fb9bafc91be5f5d04`
- New tests: `e42cc20b5bfdbbd426db11d3a9c6e44e98d39e0d7be45ac43f5cf4918c1e449f`

The independent reviewer reran the exact six-suite gate with all Mongo test
capabilities removed: **242/242 passed in 14.57 seconds**, exit status zero,
with no skips. Source/test hashes were unchanged before and after review.
Root also independently accepted the helper/service scope. The new test module
uses the `test_operational_balance*.py` naming convention so the existing
Operational and Stock gates select it without a workflow change.
The independent reviewer also executed the renamed module: **55/55 passed in
3.68 seconds**, with the same content hash and no skips. These are a subset
of the 242 cases, not additional distinct cases.

## Reproducible benchmark and limits

Run the included `report_copy_benchmark.py` with the same test interpreter. It
forbids network access, uses the existing pure fixture without modifying its
financial data, compares all twelve dense views byte-for-byte, verifies input
preservation, and then measures five alternating standard/candidate rounds.
The standard path swaps only this helper for `copy.deepcopy` in that isolated
benchmark process. Timing excludes serialization and cProfile overhead.

Final measurements on the frozen helper, after all memo-protocol safeguards:

| Round | Standard wall ms | Candidate wall ms | Standard CPU ms | Candidate CPU ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1279.606 | 994.568 | 1256.827 | 994.233 |
| 2 | 1121.397 | 1147.390 | 1120.056 | 1129.244 |
| 3 | 1246.571 | 1203.392 | 1239.998 | 1184.371 |
| 4 | 1357.089 | 1073.377 | 1343.292 | 1073.039 |
| 5 | 1181.739 | 1109.808 | 1181.474 | 1096.281 |

Median wall time fell from **1246.571 to 1109.808 ms (10.97%)**; median CPU
time fell from **1239.998 to 1096.281 ms (11.59%)**. The individual second round
was slower, so this is a small-workload median improvement, not a claim that
every request becomes faster by the same fraction.

The same run compared all twelve dense views byte-for-byte and retained the
original input. Compact input length was 6,255,750 bytes, with SHA-256
`4fda19ea047e607e409a80daf5cd2fedbf0f8d1aa2111a049447cd396f5db6c5`.
A separate final copy-stage sample measured the full-state copy at
191.510 → 112.422 ms, active obligations at 43.596 → 20.128 ms and COD metrics
at 10.485 → 5.708 ms. These stage samples are explanatory single measurements;
the whole-build table supplies the alternating repeated comparison.

This is a CPU optimization. All protective data copies still exist, so no RSS
reduction is claimed. Production reports must still be timed after deployment
at a comparable saved snapshot, together with memory during and after the
single-job child exits. Root owns independent acceptance, integration and the
governed production release.
