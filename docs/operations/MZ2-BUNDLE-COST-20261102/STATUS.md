# MZ2-BUNDLE-COST-20261102

Status: implementation, 291-case focused acceptance and full 327-case G47 gate
complete. Strict frozen-runtime gate also passed all164 responses. Local
acceptance is complete. GitHub checks run on the final branch head and their
live status is attached to the task PR. No live historical backfill has been
applied; the original80 cohort remains unverified.
Production changed: no. Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1690.

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

1. Historical reporting repair and prospective definition repair are implemented,
   with dry-run manifests, immutable evidence, conflict rejection, concurrent
   replay and rollback tests. No live apply has occurred.
2. Independent review regressions now pass, including canonical aliases, nested
   options, stale manual/plan evidence, required leaf coverage, legacy refreshes
   and read-only GET/POST cost projection parity.
3. The exact new 24-file CI acceptance command passed 291 cases, zero skips,
   failures or errors, in 23.44 seconds using isolated real Mongo fixtures.
   The existing complete G47 gate passed327 tests plus127 subtests, zero skips,
   in319.32 seconds. These results include replica and standalone fixtures.
   Code checkpoint:3e6ea31764b194432e10027c03d82c5ccf502901.
4. Related marketing, cohort and attribution projections now use the same
   historical authority without modifying the underlying ledgers.
5. Paired local benchmark: 1/80/250 orders use six logical authority reads;
   compact context plus resolution medians 6.847/73.145/229.216 ms. Large frozen
   catalog data stays in Mongo on report reads; 250-order reply bytes fall from
   13,080,413 to 2,672,413 (79.6%). This is added authority overhead, not an
   end-to-end production latency guarantee. First ordinary capture median
   8.307 ms; replay 0.025 ms with zero queries.
6. Live UI verified Salla shawl70 versus Mezan base15, rose40 and separate
   embroidery10. The original80 cohort and trustworthy historical database
   evidence have not been obtained. Available remote preview DB did not match
   the two target catalog records. Never relabel synthetic before/after as a
   production backfill result.

The initial checkpoint's CI passed20 workflows and failed2. G47 failed the
existing multi-quantity fixture because it omitted its actual count option;
that fixture now supplies the same selection used for reservation and its
focused real-Mongo rerun passes. Runtime adoption failed on malformed legacy
line placeholders; the loader now ignores those using the existing report
contract and has a passing regression.

On3e6ea317,32 of33 GitHub workflows passed, including the complete G47 workflow
and its new cost step. The remaining runtime-adoption failure compared the
intentional new source metadata and six historical reads to a no-history golden
fixture. An unmodified164-response diagnostic established zero financial,
business-field, original-collection or document-count differences. This is
diagnostic evidence, not a gate pass. Catalog-only responses now preserve the
original JSON contract; a strict audit of the six new read families is being
added while retaining the immutable response oracle and all prior read checks.
The reviewed extension now passes the strict full local gate:40 runtime cases,
164/164 exact JSON responses (including member/array order),132 requests with
exactly six audited empty historical reads and32 with none. All prior read
commands and document counts remain exact. The source/regression stage passed
115 tests plus5 subtests, including35 positive/negative read-contract cases.
Independent review found no remaining material issue. No reference oracle was
changed. See evidence/runtime-gate-summary.json for tested-source hashes.
Final-head GitHub CI remains required.

No merge, release intent, Release Guard lease, live recalculation or publish.
Issue1006 is at GitHub's2500-comment cap; this task document and Draft PR are
the continuation record. Do not edit unrelated historical comments to bypass
that limit. No customer data or credentials belong in this branch.
