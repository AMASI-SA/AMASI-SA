# Recursive copy closure: isolated lifetime experiment

Production source changed by this experiment: **none**. The experiment modifies
only an in-memory prototype, writes synthetic evidence, and denies network
connections. It does not add or replace any production copy call site.

## Demonstrated cause and minimal candidate

`operational_report_copy.copy_report_value` defines `clone` as a nested recursive
function. The function captures its own closure cell and its per-call memo. The
stdlib `_keep_alive` entries in that memo retain original containers. Therefore
the private function/cell cycle can retain both the memo and original graph until
cyclic garbage collection, even after the copied value has been returned.

The isolated prototype is generated from the exact current function AST. It
wraps only the existing final four statements in `try/finally` and assigns
`clone = None` in the finalizer. All entry/interior/exit checkpoints, traversal,
diagnostics, result, fallback and memo operations are unchanged. The protected
region includes the entry checkpoint so every exit breaks the private cycle.
The memo is not cleared.

Candidate production tail, subject to parent review:

```python
    try:
        preparation_checkpoint()
        with phase("report_copy_cpu_ms", cpu=True):
            result = clone(value)
        preparation_checkpoint()
        return result
    finally:
        clone = None
```

## Lifetime and compatibility evidence

Twelve isolated lifetime cases ran with GC disabled during observation. The
original graph was acyclic and included a weak-referenceable marker; the copied
marker was independent. Input strong references were dropped in a completed
helper frame before observation, while the successful copied output remained
retained. Explicit GC occurred only for cleanup after each observation.

| Exit | Current helper retains original without GC | Prototype retains original without GC |
| --- | --- | --- |
| Successful copy, output retained | yes | no |
| Custom hook raises ValueError | yes | no |
| Entry checkpoint cancellation | no | no |
| Interior checkpoint cancellation | yes | no |
| Exit checkpoint cancellation | yes | no |

Stdlib deepcopy also released the original immediately on success and hook
failure. After explicit cleanup collection, every original marker was gone.
The existing and prototype checkpoint counts/outcomes matched for all exits.

Six existing tests were invoked unchanged against the isolated prototype:
BSON/Python types and non-string keys, shared aliases/cycles through fallback
tuples, custom hooks/subclasses/keepalive, failure/cooperative cancellation,
atomic memo overrides, and completed-container memo restoration. All passed.
An additional check showed that an intentionally escaped custom-hook memo still
contained the original containers and correct result/alias entries after return.
Thus the prototype's lifetime result is not obtained by clearing the memo or
discarding the returned graph.

## Bounded copying measurement

The script reused the existing source-profile fixture with 2,048 synthetic
orders and four items each, for **8,192 item copies**, preserving the exact
nested product/options/taxes/custom-field shape and 75% explicit/25% label-only
option mix. The item document was 6,894,447 BSON bytes. This is a subset of the
earlier 76,604-item collector experiment, not a full collector run.

Six fresh processes ran in the order deepcopy/current/prototype/prototype/
current/deepcopy. Each process collected setup garbage before timing and kept
normal GC enabled throughout the copy loop. No per-item timer wrapper or forced
collection ran during timing. All runs proved exact complete BSON output,
unchanged input and sampled nested-output independence.

| Copier | Observed elapsed samples, ms | Median, ms | GC collections in each run (generations 0/1/2) |
| --- | --- | ---: | --- |
| deepcopy | 454.453, 445.128 | 449.7905 | 227 / 20 / 1 |
| Current helper | 372.522, 402.661 | 387.5915 | 283 / 25 / 1 |
| Prototype | 364.106, 323.617 | 343.8615 | 228 / 20 / 1 |

Observed GC elapsed times were 117.723/139.690 ms for the current helper and
109.193/109.802 ms for the prototype. RSS remained broadly similar because this
fixture intentionally retains its entire input and output throughout timing;
interpreter/setup allocations also contribute to RSS/high-water values. The
weakref observations establish the retention mechanism directly.

No measured value predicts production refresh time, establishes the production
snapshot's memory size, or authorizes blanket bundle-copy replacement. The
bounded current-helper median was already below deepcopy here, unlike the
earlier whole-collector observation; the experiments have different scale,
live graphs and instrumentation. Do not extrapolate the 43.730 ms local saving
to the 85-second production deadline.

## Exact artifacts and reproduction

- Script: `measure_report_copy_lifetime.py`
- Exact result: `measure_report_copy_lifetime_result.json`
- Python: 3.12.14
- Production copier SHA-256 before and after:
  `1679d81b0420706dc2028eaac7cd878a4cc3d1cedc650ca306c1fc4faf663eaf`
- Measurement script SHA-256:
  `f8f15a8787d64f8027047e935eb53980b351139c62b8109198d1906f98c3ba6c`

The command exited 0 and all six child processes were joined:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B \
  docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/measure_report_copy_lifetime.py
```

The script refuses to overwrite its result. Timing runs were coordinated to
avoid overlap with other agents' heavy measurements. Parent's next decision:
review the minimal lifetime fix and, if scoped, add production regression tests
for success/error/cancellation release and preserved escaped-memo semantics.
