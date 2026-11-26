# Actual collector output and retained reducer profile

## Scope and command

One authorized offline diagnostic ran against clean, frozen source
`7b4da05fba49d7b2e923ae3ac304af7e15dbcaa6`. The script and all results are
outside that source checkout. No runtime, test, deadline, query, projection or
financial formula was edited. There was no database/network/live configuration
access and no financial write. A deny-list synthetic adapter rejects unsupported
operations; its cursor returns already prepared rows rather than cloning the
fixture. No dependency installation occurred.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONHASHSEED=0 \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B \
  /workspace/scratch/1f2d9548e6ff/completion-release-prep/reducer-profile/profile_actual_collector_reducer.py \
  --output-dir /workspace/scratch/1f2d9548e6ff/completion-release-prep/reducer-profile/run
```

**Exit 0.** The one process completed in 70.029 seconds including fixture
construction, one collector pass, two instrumented reducer calls, hashing and
output generation. Its 240-second local bound did not fire. All owned async
work joined. There was no additional candidate or timing sweep.

Both root and an independent reviewer read the script before execution and
reported no blocker. The script was checkpointed on the documentation-only
evidence branch before execution; the runtime checkout remained clean.

## Why the old measurement did not answer this question

The earlier 53,997,836-byte experiment used 19,143 simple, one-item normalized
orders, then manually appended illustrative facts, provider input and issues.
Its 57,429 obligations and audit events were synthetic counts rather than actual
collector output or measured production history. That experiment correctly
measured the selected complete-copy implementation against stdlib copying, but
its uninstrumented 2.832-second candidate median did not establish the cost of
this full source cohort or the remaining production tail.

This diagnostic uses `initial('owner')`, changing only `status='active'` and
the declared start clock. It generates the same 19,151-order, four-item,
three-option fixture as the retained collector experiment, including exact
ObjectId/datetime binding metadata. The real collector is called once. The
first real `reconcile` result is the complete, unmodified input to the second
call at the same business clock. No arbitrary history, fact or balance row is
inserted to make the retained state dense.

## Valid generated scope and preservation

The real collector returned **19,151 orders and 76,604 items**, with no source
issues. Both reducer calls contain:

| Record | Count |
| --- | ---: |
| Obligations | 90,968 |
| Supplier obligations | 76,604 |
| Provider obligations | 14,364 |
| Confirmed fact records | 14,364 |
| Reconciliation audit events | 90,968 |
| Retained provider-fee order inputs | 14,364 |
| Order versions | 19,151 |
| COD reports | 4,787 |
| Manual movements, openings and requests | 0 each |

The reducer reports `provider_fee_estimate_unavailable` for all 14,364 provider
orders because the fixture has no configured fee terms. Those unknown fees are
not silently replaced with zero. The fixture contains processing orders, with
no proven delivered-driver/courier, supplier-invoice, advertising, wage or
native-statement history. Its counts are not production counts.

Assertions passed for full same-clock replay **value equality**, unchanged
source output, unchanged original/retained input states, independent successor
containers, sampled nested audit independence, joined source work and unchanged
runtime files. No new audit event or obligation appeared on replay.

The initial reducer state was 188 pickle bytes. Its first result was 38,412,489
pickle bytes; replay was 42,213,634. Pickle records identity sharing as well as
values, so the results' pickle digests differ despite exact dictionary equality.
This experiment does **not** claim byte-identical or alias-identical successors.
The input state's pre/post digest is checked independently for each call.

Preservation identifiers:

- Synthetic source input SHA-256:
  `c08140d7a748e979d6cb11c406ee43120c662ae5f576fbd2dce8b577e521c24e`.
- Full collector output SHA-256:
  `23e16abfbe0855486632c08b70d2dc71008febd99c30c55c88282104949a6339`
  (10,593,735 pickle bytes), identical to the prior actual-runtime collector pair.
- Query-sequence SHA-256:
  `ada563f25ef95250e3c6ea92a65c71054d149d68256f79fd1f7a872a28071cf1`,
  also identical to that pair.
- Manifest of 1,374 tracked non-test backend Python files:
  `d7d22629c06d14a489d8e339e45f2fe72d43728ecc9b4eec8b55996fa5a6fac4`,
  unchanged before/after. The complete per-file map is in `run/result.json`.

## Diagnostic timings and call counts

Each reducer ran exactly once under cProfile. GC stayed enabled, with an explicit
untimed `gc.collect()` immediately before each call. Profiling overhead is
substantial for these millions of small Python calls. These figures are neither
plain execution latency nor an A/B performance comparison. Inclusive entries
overlap and must not be summed.

| Measured work | First reducer | Retained replay |
| --- | ---: | ---: |
| Instrumented total wall | 15,886.088 ms | 17,430.561 ms |
| Instrumented process CPU | 15,882.155 ms | 17,426.859 ms |
| Complete independent state copy | Negligible empty state | 3,873.426 ms |
| `refresh_provider_fee_inputs` | 2,449.182 ms | 4,754.397 ms |
| All stdlib `deepcopy` calls, inclusive | 5,641.132 ms | 4,906.158 ms |
| Obligation `put`, 90,968 calls | 1,230.854 ms | 1,209.602 ms |
| Audit event fingerprint, 90,968 first-pass calls | 1,665.834 ms | No new audit fingerprints |
| GC wall | 822.035 ms | 1,274.699 ms |
| Total profiled calls | 28,952,745 | 33,141,264 |

The replay complete copy makes 3,480,939 recursive `clone` calls. Stdlib copying
makes 2,446,865 calls on replay, from 105,342 top-level calls. Both calls execute
90,968 obligation updates; replay creates no new audit rows. No dominating
quadratic scan is demonstrated by this one cohort; ordinary per-component work
and graph copying dominate the recorded profile.

## Concrete repeated work still present

`operational_balance_engine.reconcile` first creates the full independent
successor with `copy_report_value(state)` at line 240. It then invokes
`refresh_provider_fee_inputs(result, sources)` at line 278.

Inside `operational_configured_provider_fees.refresh_provider_fee_inputs`,
line 310 deep-copies the retained `previous['orders']` map again. The following
loop separately copies current fields into each eligible order at line 331.
For this replay, every one of the 14,364 retained order entries is subsequently
replaced by its current source projection. Thus the old provider graph is
first included in the full successor copy, copied again locally, and then
discarded in favor of current per-field copies.

The helper has 86,187 direct calls to `deepcopy` in both first and replay passes:
86,184 current-field copies (14,364 orders times six fields), one complete
retained-map copy, one settings copy and one policies copy. Their combined
cumulative profiled work is 2,302.086 ms on the first pass and 4,617.207 ms on
replay. Python 3.12 inlines the comprehension; cProfile assigns all these calls
to the same enclosing function rather than distinct call sites. It therefore
cannot give the exact elapsed time of line 310 separately from line 331. The
source and recorded counts establish the repeated graph copy, but subtracting
the two combined timings would not be an exact retained-copy measurement or a
production saving estimate.

There is only one production call site for this helper: `engine.reconcile`.
Existing tests also call the helper directly. An ownership-only shortcut is
not established: the retained-adjustment branch mutates saved nested rows and
the old provider graph can alias other state. Merely replacing the deep copy
with the original map would not preserve isolation. Any change must retain
unseen orders, duplicate-source behavior, retained adjustments, independent
source and state graphs, malformed/custom fallback, error order and all
settings/policy/statement history.

Root identified a smaller alternative for later consideration: use the already
verified complete independent `copy_report_value` only for the one full retained
map copy, while retaining every small current-field copy and the direct helper's
isolation contract. This has not been benchmarked or implemented here; it is a
candidate requiring its own focused parity/effectiveness evidence.

This diagnostic made **no such implementation change or candidate measurement**.

## Remaining claim limits

The old live attempt's 12.938-second **cancelled** reconciliation stage remains
evidence that the remaining tail can be material. Its runtime and data differ
from this diagnostic, and cProfile times cannot be compared directly with that
live measurement. The old initial-copy improvement does not remove the repeated
provider input work identified here. This is a concrete follow-up opportunity,
not proof that it caused the entire live delay or that changing it would make
the whole request complete.

No bank claim preparation/arbitration, party catalog I/O, recovery-proof
generation, packet encoding/compression, archive staging, CAS, concurrent profit
work or request/lease admission is included. RSS is one process's cumulative
high-water (930,044 KiB) and includes fixture preparation; it is not a measured
reducer peak or a production memory requirement.

A complete new production save and preservation checks remain necessary before
claiming the unchanged 85-second workflow succeeds.

## Artifact hashes

| Artifact | SHA-256 |
| --- | --- |
| `profile_actual_collector_reducer.py` | `1cbda56ef7fd0ad64fb9a9b10c33f30cbc69111ea2d6e5c547293f91b3f21fff` |
| `run/result.json` | `be1b40e00a9c339859229bdaba01fbe4bdf6e9d4eac67f87bf19a39cb460a257` |
| `run/first_reconcile.pstats` | `550510c077276299a776c8eb300d9240ff170a8bdb9b5d6c2c68a90e866f8eac` |
| `run/retained_replay.pstats` | `107cfb22748ef1051e3b3f37df4d2145a90de33c64d12e6177cb6e3712482a5b` |
