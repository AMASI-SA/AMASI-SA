# Current refresh CPU measurements — synthetic only

## Scope and identity

- Baseline source: merged M3 `2c875b5f16b0d58e46effdd7acd28a91041b62fb`.
- Tree: `706980488f7d42f75d40bd789922b6ea7be69ac0`.
- Isolated worktree: `mezan-operational-refresh-stage-analysis`.
- Local Python: **3.12.14**.
- Initial measurements did not modify application source. The separately
  authorized piece-index follow-up below changes only that matching path.
- Frozen releases, production, live Mongo, browser and GitHub were not modified
  or accessed by this work.
- All records are generated in memory. Existing deny-list source and lease
  fixtures exercise current code without a database server or network.
- Timings below use ordinary dictionaries and lists. Operation-count samples
  are separate: per-row instrumentation is intentionally excluded from timing
  conclusions. These are single local observations, not live latency evidence.

## 1. Actual collect_sources: item × preparation-piece scans

Every synthetic order has one valid product line with a known SAR 20 cost. All
preparation pieces have valid-shaped but unrelated order/item identities.
The actual collector returns all orders with unchanged inputs and costs.

| Orders/items | Preparation pieces | Plain elapsed | Matching visits implied by code |
| ---: | ---: | ---: | ---: |
| 500 | 0 | 0.051703s | 0 |
| 500 | 5,000 | 0.175202s | 2,500,000 |
| 1,000 | 10,000 | 0.554121s | 10,000,000 |
| 2,000 | 20,000 | 2.512664s | 40,000,000 |
| 5,000 | 20,000 | 5.229732s | 100,000,000 |

A separate count sample through the exact matching comprehension recorded
**1,000,000 visits for 500 items × 2,000 pieces**. This establishes O(I×P)
behavior independently of wall time. The instrumented sample took 5.681653s;
that number reflects instrumentation and must not be used as production cost.

Cancellation uses the real visible-refresh lifetime with a disconnect queued
just before the first matching scan. Piece dictionaries remain ordinary, and
one callback wraps the existing per-item cost function. Of 200 possible items,
only **one** entered cost calculation, with 20,000 pieces in its matching scan.
The response was **499**, heartbeat delay **16.845ms**, and return after
**17.031ms**. The lease was released and no task remained.

**Assessment:** proven avoidable multiplication, but this measured 5.23s case
does not independently explain the observed 85s live timeout. Do not call it
the dominant live phase without the live cohort size or phase timing.

A potential narrow index must key by
`(str(piece.get('order_number')), physical_order_item_id(piece))` after the
existing experimental-piece filter. Each value must remain an ordered list
containing every original position. Deduplicating pieces or units would change
the existing duplicate/quantity-coverage conflict rules. Building that index
must retain cooperative cancellation. No index had been implemented at this
initial measurement; the subsequent authorized implementation is recorded below.

## 2. Actual new-bank audit captures

The measured path is the unchanged `operational_balance_store.audit()` →
`capture_after_balances()` → `_cash_only_projection()` for each newly appended
automatic bank receipt. Every prefix balance is checked against an independent
arithmetic expectation. Claims/database waits are not included in these CPU
measurements.

| Saved movements M | New receipts K | Plain elapsed | Prefix movement visits |
| ---: | ---: | ---: | ---: |
| 500 | 100 | 0.194917s | 55,050 |
| 2,000 | 100 | 0.808600s | 205,050 |
| 2,000 | 500 | 3.805844s | 1,125,250 |
| 5,000 | 500 | 8.234589s | 2,625,250 |
| 5,000 | 1,000 | 17.821704s | 5,500,500 |

The count formula is **K×M + K×(K+1)/2**. A separate sample with 1,000 saved
movements and 200 new receipts measured exactly **220,100 movement visits**.
All sequential after-balances were exact in every sample.

A cancellation sample uses the actual service refresh, real claim/store gates
and an in-memory fixture, isolating unrelated source/reconcile/profit stages.
It queues disconnect immediately before the first cash projection with 20,000
saved movements. That one synchronous projection finishes in **66.862ms**;
heartbeat delay is **67.217ms**, and response 499 returns after **67.436ms**.
Exactly one pinned claim intent remains, the second claim is not admitted,
the saved financial state is unchanged, the lease is released, and there are
zero pending tasks.

**Assessment:** a concrete cumulative CPU risk for a large new-claim cohort,
before serial authorization/database costs. It is not a detached-work leak.
If the live refresh has **zero new eligible bank receipts**, this phase is
absent and must be discarded as the explanation for that refresh's timeout.

A potential optimization must be scoped to this one refresh attempt's
automatic-bank sequence. It may initialize current bank/currency balances once
and update the applicable balance after each admitted append. It must preserve
each prefix audit snapshot, names, refund and cross-currency arithmetic,
opening-presence rules, malformed-history unavailability, timestamps/seals,
fresh claim admission, and CAS retries. A cache across requests or the general
manual-writer path would violate the current-state contract. No optimization
was implemented or claimed verified here.

## Reproduction

From the isolated worktree, with the existing Python environment:

```bash
PYTHONPATH=backend:backend/tests /workspace/scratch/af49aae2c7d1/.venv/bin/python -B scripts/testing/operational_refresh_stage_analysis/measure_hotspots.py sources
PYTHONPATH=backend:backend/tests /workspace/scratch/af49aae2c7d1/.venv/bin/python -B scripts/testing/operational_refresh_stage_analysis/measure_hotspots.py bank
```

Run the two measurements sequentially. `source_results.json` and
`bank_results.json` retain the observations and exact current source hashes.

The first harness launch encountered a Python 3.12 comprehension-inlining
instrumentation issue before measurements. Only the harness was corrected to
recognize both inline and separate comprehension frames. The first source
cancellation probe used a per-piece counter and inflated latency; the retained
probe instead uses one callback per item with plain piece dictionaries.

## Authorized follow-up: preparation-piece index

The measured multiplication was subsequently authorized for a narrow fix.
`operational_piece_index.index_preparation_pieces()` now builds ordered lists
using the existing `physical_order_item_id()` normalizer. `collect_sources()`
builds it once with the existing joined `prepare_in_thread()` lifetime and
uses one tuple lookup per item. The helper checks cancellation for every row.
The existing experimental filter, piece/unit coverage checks, amounts, source
read limits, authorization and CAS paths remain unchanged.

No new-bank audit optimization was implemented. Its live new-claim cohort is
unknown and the observed saved bank cohort was too small to establish it as
the cause of the production timeout.

### Exact old/new actual-collector measurements

The old collector is loaded from local Git at exact M3, without changing the
working source or any frozen worktree. Both variants use the same synthetic
input builder, ordinary dictionaries and existing in-memory source fixtures.

| Orders/items | Pieces | M3 elapsed | Indexed elapsed | Source read calls, both |
| ---: | ---: | ---: | ---: | ---: |
| 500 | 0 | 0.059138s | 0.047856s | 42 |
| 500 | 5,000 | 0.226300s | 0.071840s | 42 |
| 1,000 | 10,000 | 0.743236s | 0.162922s | 50 |
| 2,000 | 20,000 | 2.315446s | 0.346196s | 66 |
| 5,000 | 20,000 | 5.494091s | 0.662756s | 114 |
| 20,000 | 20,000 | **22.897864s** | **2.295125s** | **354** |

At the supported 20,000-row caps, actual collection is approximately 10 times
faster in this fixture, saving 20.60 seconds. These are local synthetic timings,
not a claim that production refresh now completes within 85 seconds.

The separate operation-count cohort of 500 items and 2,000 pieces records
**1,000,000 old piece visits → 2,000 index-build visits**. The actual collector
test separately verifies one lookup per item. The proven operation counts at
the caps are therefore 400,000,000 scan visits versus 20,000 index visits plus
20,000 lookups; the very large case is timed without per-row instrumentation.
Every measured cohort returns the same number of known-cost orders and leaves
the input unchanged.

The post-index conversion cancellation sample returns 499 after 0.300ms,
with a 0.151ms heartbeat delay, one item entered, no pending tasks and the lease
released. A separate actual-refresh regression interrupts the owned index
thread at its tenth piece and verifies it is stopped and joined before return.
The existing conversion/preparation lifetime tests also pass.

### Focused verification

**78 passed in 4.52 seconds**, including 13 new tests:

```bash
PYTHONPATH=backend:backend/tests /workspace/scratch/af49aae2c7d1/.venv/bin/python -B -m pytest --noconftest -q -p no:cacheprovider --asyncio-mode=auto --tb=short backend/tests/test_operational_balance_piece_index.py backend/tests/test_operational_balance_sources.py backend/tests/test_operational_balance_source_fixes.py backend/tests/test_operational_balance_source_conversion_lifetime.py
```

New coverage includes exact string/physical-item normalization, unusual legacy
identity types, original row order and duplicates, whole-collector differential
fixtures against the old scan, partially received pieces, malformed item IDs,
experimental filtering, duplicate-unit ambiguity, bounded index/lookup counts
and joined disconnect cancellation during the actual index build.

Reproduce the two performance samples sequentially:

```bash
PYTHONPATH=backend:backend/tests /workspace/scratch/af49aae2c7d1/.venv/bin/python -B scripts/testing/operational_refresh_stage_analysis/measure_hotspots.py sources --baseline-ref 2c875b5f16b0d58e46effdd7acd28a91041b62fb
PYTHONPATH=backend:backend/tests /workspace/scratch/af49aae2c7d1/.venv/bin/python -B scripts/testing/operational_refresh_stage_analysis/measure_hotspots.py sources
```

`source_before_index.json` and `source_after_index.json` retain complete results
and exact runtime source hashes. The initial source and bank measurement JSON
files retain the preceding, explicitly synthetic analysis.
