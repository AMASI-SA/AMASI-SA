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
