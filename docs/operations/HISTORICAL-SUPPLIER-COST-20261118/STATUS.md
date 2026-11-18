# Historical supplier cost in profitability reports

Task: HISTORICAL-SUPPLIER-COST-20261118.

Repository: AMASI-SA/AMASI-SA. Task branch:
`codex/historical-option-cost-20261118`.
Reviewed starting production branch: `hotfix/prod-snap-meta-final`,
commit `68035e55018d5b75bdd6b9e0ab6000d7528a956f`.

## Owner-requested behavior

The executive profit summary, best-selling products card and detailed sold
products page must use the same cost rule for their selected sales cohort:

- Unreceived ordinary units use the product and customer-option cost setup.
- Supplier-received units use the approved supplier invoices linked to their
  exact order line and physical unit, rather than later product options.
- Partially received lines retain actual and expected quantities separately.
- Supplier product prices already include direct option surcharges; invoice
  service amounts are separate. Replace the covered estimates once. Preserve
  internal packaging/material costs that supplier invoices explicitly exclude.
- Do not silently mark real missing cost, missing invoice proof, contradictory
  identity, or ambiguous receipt coverage as complete.

The existing sales-date/status cohort remains unchanged. This is not a new
report of purchase cash flow or supplier invoices selected independently of
sales. No supplier, financial, order, catalog, or historical snapshot data is
rewritten by report reads.

## Diagnosed cause

Current ordinary reporting can use catalog option bindings added after a sale.
An absent historical selection becomes `option_selection_unresolved`, even
when the supplier has already invoiced the received unit. The report's existing
historical cost loader does not read ordinary supplier invoices. Changing only
the warning label cannot repair executive cost completeness.

Native invoice evidence separates product, service and input-tax amounts;
receiving events and preparation pieces identify exact units. Invoice product
cost excludes internal material resources. Configuration-entry timestamps are
not treated as provider option creation dates.

## Implemented source

A bounded, tenant-scoped read-only supplier-cost projection now feeds shared
resolution in the three requested report surfaces. Existing historical APIs,
bundle authorities and approved corrections remain protected. Complete received
slices remain visible when another quantity on the same line is incomplete.
Every newly committed real supplier invoice advances the report cost revision,
including an invoice whose price matches current configuration. The increment
is inside the existing close transaction; replay, experiments and rollback
cannot publish an extra real cost revision.

Independent reviews verified native invoice net amounts, exact physical-unit
coverage, posted journal digest and reversal checks, optional legacy variant
compatibility, original selection contradictions, completed service scope and
quantity reconciliation. Saved internal material scope is preserved. Evidence
that does not prove a missing material cannot be used to suppress its warning.
Mixed supplier-invoice/Salla fallback retains its existing visible source label
on screen and in print.

The frozen runtime gate keeps its existing JSON and raw query/document oracle.
Only two independently specified empty receipt reads per 250 selected orders
are added. Negative tests reject changes to scope, projection, event type,
multiplicity, document counts and unexpected invoice reads. Reviewed source pins
retain cooperative parity and the fixed scheduling budgets.

## Validation checkpoint

Baseline, before integration: 107 tests passed in the existing dashboard cost,
sold-product details, order cost authority and option identity suites. Two
existing Pydantic deprecation warnings; no baseline test failures.

Final combined local verification: **303 passed, 5 subtests passed**, with two
existing Pydantic deprecation warnings. This covers the shared helper, all three
report surfaces, historical/bundle/option behavior, supplier lifecycle/service
contracts, exact source/cooperative parity and 85 read-gate checks. Independent
financial/identity review and integration/read-gate review found no remaining
blockers after their reproduced defects were fixed. `git diff --check` passes.

Four native-writer/transaction tests collect and await real-Mongo execution.
The task's isolated local environment cannot run a Mongo server:
MongoDB 8.0.12 fails its CPU topology check; the repository's alternative 7.0.16
exits with `open: Operation not permitted`. This does not waive the real-Mongo
CI gate. No connection to a production database has been attempted. Frontend
dependencies are not installed locally; the existing exact-head CI runs the
modified badge test and production build.

## Release state and next action

PR: https://github.com/AMASI-SA/AMASI-SA/pull/1989.

### First exact-head CI and reviewed follow-up

Candidate `6664a4600fa7218189666ab07e4a7b126fdedbf9` passed the sold-report
workflow (379 backend tests, 131 frontend tests across 8 suites), the supplier
display frontend (58 tests and production build), and the native dashboard
runtime gate. That gate compared exactly 164 responses in each of three arms;
all four XML reports had zero failures, errors and skips. Its regression step
passed 271 tests and 5 subtests.

All four new native invoice/report revision tests passed on the disposable
Mongo replica set. Three other assertions correctly blocked the first candidate:
one duplicate dashboard source pin, the raw accounting collection boundary,
and the protected invoice-close source hash. These were resolved as follows:

- The duplicate pin now names the same independently reviewed reducer; its
  observer, cooperative parity and read-count assertions remain intact.
- Journal proof now passes through a narrow public accounting API with fixed
  owner/ID/reversal scope and projection. The raw-access gate is unchanged.
- Only the exact reviewed transactional revision condition is normalized in
  the invoice-close gate; every original financial function/file hash remains.

The follow-up accounting/helper/report suite passed 142 tests. The observer and
financial-boundary checks passed 21 tests. Independent review approved the
follow-up with 108 focused tests passing. The new consumer has no accounting
AST access violations. The optional repository-wide validator's six private
imports in four unrelated modules were confirmed unchanged from the production
base; those modules and that validator were not modified.

The native invoice workflow now runs the new proof and revision tests when
the accounting API changes. Clean final-head CI, including the previously
blocked 200-piece close/concurrency/rollback stages, remains required. The PR
body records the latest exact candidate SHA and final CI handoff evidence.

Production changed: **NO**. No merge, Release Guard lease, adapter rehearsal,
publish, database repair, supplier write, or application release performed.
The earlier Android/shipping publication belongs to a different completed task
and is not authorization to publish this cost change.

An initial immutable WIP checkpoint is saved on the dedicated remote task
branch. Next: save the final reviewed candidate and Draft PR, run required CI
(including real-Mongo transaction tests), and present the concrete result.
Production publication requires the owner's
authorization for this new change and the repository's unchanged protocol-v5
release procedure.

## Authorized performance follow-up

The owner approved implementing the performance improvements after the load
review. The previously tested candidate is
`bcdd6e501e629ee715667c39d1b43aab5b263914`; its exact-head CI completed with
51 successful and six explicitly nonapplicable skipped jobs, no failed or
pending checks. The PR body preserves the detailed immutable CI links.

The performance work remains source/testing only. Production changed: **NO**.
No new publication, production benchmark, data migration or index operation
has been executed. No scheduler, provider synchronization or report refresh
cadence is added or changed.

### Index milestone

Four nonunique, unfiltered query indexes match the owner/order, owner/invoice
identity and owner/journal identity/reversal selectors. Narrow piece/invoice
index builders run during existing release-global startup before readiness,
without invoking unrelated unique operational indexes. Canonical domain index
builders use the same definitions. Accounting indexes use the existing
accounting startup path. Report GETs still do not perform DDL.

Duplicate, draft and cross-operation financial evidence remains visible;
these indexes do not turn contradictory records into acceptable proof.
The preparation router's once-per-instance/retry test now expects six index
definitions instead of five. Existing supplier index profiling still wraps
the complete original initializer.

Local verification: 50 index, fulfillment-read and accounting tests passed.
Independent review: 59 tests and five subtests passed across those boundaries,
financial hashes and exact dashboard source contracts. A review caught and
corrected decorator placement before checkpoint. Real-Mongo query plans and
receipt-heavy performance measurements remain pending.

### Remaining performance work

Preserve fresh invoice/journal reads. Reusing a prior Mongo document ID or
stored digest without comparing current full invoice content could hide an
edit; use only bounded request-local memoization of pure allocation/digest
work for exact-equal invoice content. Keep the standalone sold report fresh:
the current cost revision does not version all order/snapshot changes, so
adding a persistent report cache would change its freshness contract.

Next: complete and independently review the exact-content optimization; run
synthetic month/two-month receipt-heavy A/B measurements, concurrency, memory
and real-Mongo explain checks in a disposable CI database; repeat financial
parity gates and save a remote checkpoint. Timings from synthetic fixtures
must not be presented as measured production latency.

Production branch advanced again to
`d3632a33843e076dd7824933528dcc7d897bc32a` while this branch remained isolated.
The additional upstream source must be preserved and integrated before any
eventual publication; the prior exact-head evidence is not evidence for a new
merge identity. The latest task checkpoint SHA is recorded in the PR body.
