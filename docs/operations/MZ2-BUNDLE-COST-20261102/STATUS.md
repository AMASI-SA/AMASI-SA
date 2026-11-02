# MZ2-BUNDLE-COST-20261102

Status: WIP, not a release candidate. Production changed: no.

Repository: AMASI-SA/AMASI-SA. Task branch:
`codex/mz2-bundle-cost-snapshots-20261102`.
Baseline: `70576e2a06237eb193b1c927f11ced92e2229589`.

## Contract

One shared order-cost authority must feed dashboard product rows, executive
profit, sold-product reports and related campaign profitability. A synthetic
physical base15 plus conditional component40, quantity4, costs220 SAR. A
composite container must not acquire an extra parent cost; represent a physical
base through the existing current_product rule. Optional embroidery remains
separate and must not be dropped to force an expected total.

Historical catalog edits must not reprice stored orders. Bundle intake, plans
and complete consumed-unit evidence outrank live catalog estimates. Missing or
changed evidence must remain unresolved. Return policy remains once per order.
Historical correction may alter reporting projections only, never approved
journals or operational stock/reservation records.

## Completed initial slice

- Shared decimal SAR catalog calculation and tenant-scoped batch historical
  authority, without report-time mutations or per-order bundle queries.
- Normal/cooperative dashboard builders and sold-product HTTP identity
  projection integrated. Sold-product loader no longer calls order_view per order.
- Five Snapchat readers attach the same scoped authority; existing cache keys
  and refund policy placement retained.
- Ordinary costs freeze in the existing lifecycle transaction. Snapshot POST
  uses first-write persistence; expected-to-actual transitions retain history.
- Bundle intake stores quantity and economic-line fingerprint for new records.

## Initial verification

Commands run with isolated `/tmp/mezan-bundle-cost-venv/bin/python`.

- Before producer fix: replay regression FAIL for the intended reason,
  first recorded60 became280 after catalog edit.
- Shared authority suite:15 passed, exit0.
- Reporting slice:39 passed,1 real-Mongo test not run before fixture setup.
- Snapchat slice:87 distinct tests passed across two runs (10 overlap).
- Old Snapchat calculation executed against new220 test: FAIL,60 versus220.
- Source parity/profit quality:10 tests +5 subtests passed.
- compileall and git diff --check: exit0.

These are slice results, not an integrated final verdict. Real transaction
fixtures, independent review, historical correction, performance evidence and
fresh final verification remain pending. Current test runtime is being aligned
with repository dependency versions before final verification.

## Blockers and next safe actions

1. Finish auditable, dry-run-first historical reporting correction and precise
   configuration repair using existing current_product semantics.
2. Complete real Mongo transaction/replay/concurrency tests.
3. Resolve independent review findings and run combined acceptance.
4. Record benchmark, before/after report and current CI.

No merge, release intent, Release Guard lease, live recalculation or publish.
Issue1006 is at GitHub's2500-comment cap; this task document and Draft PR are
the continuation record. Do not edit unrelated historical comments to bypass
that limit. No customer data or credentials belong in this branch.
