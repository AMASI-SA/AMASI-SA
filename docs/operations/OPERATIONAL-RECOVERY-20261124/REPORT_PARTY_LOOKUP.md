# Saved financial report CPU evidence

These measurements use the existing pure `cod_fixture` in
`backend/tests/test_operational_balance_cod_projection_performance.py`. They
read no database, request no external source and write no financial state.
They do not establish Production page loading times or a Production database
query bottleneck.

## Measured bottleneck

Every scoped financial-position view builds the shared saved financial report
before selecting its displayed tables. With 20,000 synthetic movements and 100
COD claims, a pre-change cProfile run of the summary took 1,654 ms under the
profiler; `operational_balance_service.report` accounted for 1,648 ms. These
profiled durations include instrumentation overhead and are not ordinary
response times.

The largest contributors were:

| Function | Calls | Cumulative profiled time, ms |
|---|---:|---:|
| `copy.deepcopy` | 525,164 | 884.7 |
| Report-local party lookup `get` | 40,101 | 284.5 |
| `party_key` | 40,101 | 267.3 |
| `operational_balance_store.digest` | 40,101 | 253.5 |

The hashing rows are nested contributions, not additive. Advertising and
driver views showed the same dominant shared report cost. An unprofiled timed
wrapper attributed 211.3 ms to the whole-state copy at the start of `report`,
compared with 0.47 ms for its separate obligation copy and 0.36 ms for its COD
projection copy. Those copies remain unchanged because they provide mutation
isolation during report preparation.

Before the memo, three warm repeats of all twelve views gave these medians:

| View | 5,000 movements, ms | 20,000 movements, ms |
|---|---:|---:|
| summary | 108.665 | 561.723 |
| advertising | 108.313 | 461.035 |
| drivers | 117.860 | 443.633 |
| couriers | 128.200 | 463.457 |
| providers | 108.063 | 462.566 |
| employees | 109.470 | 528.738 |
| accounts | 118.497 | 474.037 |
| suppliers | 114.062 | 472.925 |
| other | 107.052 | 457.347 |
| expected | 113.947 | 475.453 |
| forecast | 137.698 | 465.366 |
| all | 106.670 | 491.694 |

## Narrow implemented change

The local `get` function now reuses the original digest for a repeated
`(party_type, party_id, currency)` tuple during one report call. The memo has a
hard cap of 4,096 entries. Additional parties follow the original uncached
path, so reaching the cap cannot omit financial rows. Only exact string types
are memoized: legacy nonstring identities retain the original digest behavior,
including distinctions between boolean, numeric and string values. The memo
expires when the report returns. There is no shared cache, revision lookup,
permission change, source read, financial calculation or database write.

The original party keys, insertion order, amounts and output names remain
unchanged. No copy or calculation pass was removed.

## Implemented comparison

After a small warm-up, five paired runs built the full summary with the memo
disabled (`_REPORT_PARTY_KEY_CACHE_LIMIT = 0`) and with its ordinary 4,096-entry
cap. Both runs use the same report code and fixed `generated_at`. This switch
only controls memo storage and reproduces the former repeated hashing path.

| Movements | Claims | Uncached median, ms | Memoized median, ms | Median decrease |
|---:|---:|---:|---:|---:|
| 5,000 | 100 | 114.774 | 86.143 | 24.9% |
| 20,000 | 100 | 454.177 | 359.470 | 20.9% |

Ranges were 109.172–119.318 versus 75.732–107.028 ms for 5,000 movements, and
422.456–486.357 versus 310.810–385.152 ms for 20,000. Every paired report had
identical serialized JSON bytes and the saved input digest remained unchanged.
Other local work can affect absolute timing, so these values are synthetic
CPU measurements, not a live performance commitment.

## Verification

The fail-first regression counted 901 identity hashes for 400 movements before
the change; the new path computes two hashes for its two parties, and computes
them again for a second independent report call. The bounded-cache test uses a
small cap and verifies that uncached parties and their financial totals remain
present. Additional tests compare exact JSON bytes for all twelve views of the
existing multi-party, multi-currency `rich_state` fixture, check input
immutability and cover legacy nonstring identifiers.

The focused command passed 67 tests:

```bash
PYTHONPATH=backend:backend/tests python -m pytest -q \
  backend/tests/test_operational_balance_report_party_lookup.py \
  backend/tests/test_operational_balance_cod_projection_performance.py \
  backend/tests/test_operational_balance_report_metadata.py \
  backend/tests/test_operational_financial_position_views.py
```

Cross-request sharing of the shared report and changes to its large copy were
not introduced. They require separate revision, authorization, cancellation,
memory and mutation-isolation analysis. Production data-wait observations
still require their own browser and server measurements after deployment.
