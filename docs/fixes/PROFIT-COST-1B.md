# PROFIT-COST-1B — Financial cost completeness

## Scope and base

- Base: `origin/hotfix/prod-snap-meta-final@1de6118484ac4fe1d0981e230618dbb573d8c58c`.
- Branch: `codex/profit-cost-financial-completeness`.
- Diagnosis reference: Issue #1006 comment `5555510594`.
- This change does not depend on PR #1013. `dashboard_v2_routes.py` is identical
  between the base above and accepted PR #1013 head
  `6aecb923f617979106b856fe6e55e718d5c52bd8`.

## Root cause

The line-cost classifier already separated two independent facts:

1. `mezan_cost_complete`: an explicit Mezan cost is configured.
2. `calculation_cost_available`: an actual cost is available for financial
   calculation, including the established Salla fallback.

`build_mezan_v2_product_cost` nevertheless used the first fact to populate the
only order-incompleteness counter consumed by `mezan_profit_engine`. A Salla-only
line therefore contributed its actual amount to product cost and net profit,
while the same order was marked incomplete and blocked by the scale gate.

## Contract before and after

| Axis | Before | After |
| --- | --- | --- |
| Mezan setup | Legacy `missing_products_count`, `missing_product_cost_count`, and `incomplete_orders_count` | Legacy fields remain unchanged; explicit `mezan_setup_missing_products_count`, `mezan_setup_missing_lines_count`, and `mezan_setup_incomplete_orders_count` document the same administrative units |
| Financial cost | Inferred incorrectly from Mezan setup | Versioned `financial_cost_missing_products_count`, `financial_cost_missing_lines_count`, and `financially_incomplete_orders_count`, derived only from `calculation_cost_available` |
| Salla fallback | Amount included but accounting incomplete | Same amount/source; Mezan setup remains missing, financial cost is complete |
| Both sources missing | Base silently contributes zero to the numeric subtotal and quality is incomplete | Numeric behavior is unchanged, including known partial components; financial quality explicitly remains incomplete and the gate blocks scale |
| Explicit zero | Available at the line resolver | Remains available; strict counter parsing does not replace zero with a legacy value |

The financial counter contract is
`mezan_financial_cost_completeness_v1`. Its required fields are all-or-nothing.
If any versioned field is present, the version and all three non-negative integer
counters must be valid. Partial, null, boolean, string, negative, or unknown
versions fail closed; they are never completed from legacy counters. If the new
contract is entirely absent, old snapshots retain the conservative legacy
interpretation.

### Legacy quality round-trip follow-up

The first compatibility implementation parsed a complete legacy payload
correctly, but then exported all four names from the versioned contract with
`None` values. After JSON serialization those names made the next reader treat
the result as an explicitly supplied, invalid versioned contract.

The reader now keeps resolved counters and their source as internal conversion
state. Public quality output includes versioned contract fields only when those
fields were actually present in the input. A legacy conversion therefore stays
legacy across a JSON round-trip and does not invent a missing-lines count. An
explicit partial, null, wrong-version, or otherwise invalid new contract keeps
its supplied fields in the output and remains fail-closed; a payload-provided
`financial_contract_present=false` cannot hide those fields.

The compatibility aliases in the profit envelope
`missing_product_cost_count` and `incomplete_profit_orders_count` now report the
financial counters for decision consumers. Dashboard product alerts and filters
continue to read the unchanged legacy/Mezan-setup fields.

## Connected regression evidence

The primary regression uses the real chain:

`build_mezan_v2_product_cost`
→ `build_mezan_profit_envelope`
→ `require_profit_accounting_complete_for_scale`.

Only unrelated I/O boundaries (orders, settings, ads, shipping, payroll, and
recurring obligations) use isolated in-memory fixtures. Product resolution,
line cost, aggregation, quality construction, envelope arithmetic, and the gate
are not mocked. The fake collections expose reads only and fail immediately if
any provider/accounting write method is requested.

Red on the base:

- Salla product cost: SAR 60.
- Net profit: SAR 120.
- Failure: `quality.complete` was `false` before the gate could pass.

Green after the change:

- The same cost and profit amounts remain SAR 60 and SAR 120.
- Mezan setup still reports one missing product.
- Financial missing product/line/order counters are all zero.
- Accounting completeness and scale-safety are true; the unchanged scale gate
  accepts the accounting evidence only.

The suite also covers Salla variant, Mezan-only and mixed sources, missing base,
partial component/service cost, no product lines, explicit zero, quantities,
selected options, duplicate binding IDs, invalid/partial contracts, legacy
payloads, incomplete advertising, another missing accounting component, and
unchanged pause/reduce behavior.

Red-before for the round-trip regression produced three expected failures: a
complete legacy totals conversion, a complete legacy envelope conversion, and
the legacy output of `_accounting_quality` all became incomplete on their
second read. The six controls for incomplete legacy and valid/invalid new
contracts passed. Green-after: all nine focused cases passed.

Fresh local affected-suite result: 136 tests passed. This includes the connected
Salla-only assertion that product cost remains SAR 60 and net profit remains SAR
120, plus the advertising and other-component blockers. Two existing Pydantic V1-style
`root_validator` deprecation warnings were emitted from
`recurring_obligations_routes.py`; they are unrelated to this change.

## Synthetic benchmark

This is an isolated in-memory benchmark, not Production data. The controlled
fixture contains exactly 5,000 catalog products and 25 one-line Salla-only
orders. Five runs were measured with Python `perf_counter`, `tracemalloc`, and
compact UTF-8 JSON serialization.

| Measurement | Base median/value | After median/value | Delta |
| --- | ---: | ---: | ---: |
| DB query count | 6 | 6 | 0 |
| Processing time | 392.176 ms | 390.967 ms | -1.209 ms (-0.31%) |
| Traced peak memory | 4,819,915 B | 4,819,939 B | +24 B |
| Response size | 18,147 B | 18,816 B | +669 B (+3.69%) |
| Product-cost total | SAR 750 | SAR 750 | 0 |

Timing is a local synthetic measurement with normal run-to-run variance. The
structural guarantees are that counters are computed during the existing order
line traversal and the change adds no query, catalog scan, cache, worker, or
network operation.

## Compatibility and overlap

- PR #1004: no changed-file overlap. Its incomplete-ad-spend contract remains a
  separate blocker and has a dedicated regression here.
- PR #1013: no changed-file overlap and no stacked dependency.
- EXIT-2A: no matching branch/file reference was found in this checkout, and no
  conversation/runtime/packaging file is changed.
- Legacy product alerts and `missing_mezan_cost` filters retain their meanings.
- The scale gate, owner approval, identity, freshness, action permissions, and
  all other execution gates remain in place.

## Explicitly unresolved

- The owner-approved 35% estimator still needs a separate basis and scale
  reliance contract. This change neither implements nor cancels it.
- Historical snapshot read authority and stored `total_product_cost` are
  unchanged.
- Resource-binding de-duplication is unchanged.
- GOAL-PROGRESS-1, DI-EVIDENCE-1, Phase 5, and Action Gate behavior are outside
  this task.
- No resync, backfill, provider call, accounting write, release intent, release
  lease, publish, or Production operation is part of this change.
