# Shipping and payment-fee reconciliation

## Scope

Repair acquisition of delivered courier shipments and their native delivery history. Preserve shipment identity, event provenance, period boundaries and idempotent fee recognition. Reconcile payment-fee calculation with owner-supplied settlement evidence and persist supported terms through the existing owner settings flow.

## Baseline

- Repository `AMASI-SA/AMASI-SA`
- Branch `codex/shipping-fees-reconciliation-20261104`
- Base `cec4c3854031c357577f07568f91a44c082905fc`
- Production source observed at task start `424525026a2224ca805e091e163353641c0e2012`
- Release Guard status observed inactive before any shared workspace change

## Verified checkpoint

The component contract and owner form preserve an explicit VAT rounding mode. Existing components default to half-up. Capture-only policies can retain unknown refund and cancellation treatment until the corresponding executed event requires it. An evidenced card network resolves an enclosing wallet label while conflicting networks remain unresolved.

The fee-table API suite passed eleven tests against isolated Mongo, covering versioned edits, concurrency, scope restrictions, source activation, preview and financial-state fingerprints. The owner form has thirteen-row pagination, editing, revision history and a direct owner navigation link. Component metadata, fee limits, optional legacy suggestions and event-specific previews passed focused and independent verification.

The final combined operational suite passed 2,694 tests and eighteen subtests with isolated Mongo. The shipment and DTO suite passed 357 tests, and its expanded service and refresh suite passed 374 tests. The combined operational frontend run passed 414 tests. Four additional completeness UI tests passed. Independent replay confirmed source identities, timezone semantics and repeat-run stability, including forty rejected ambiguous or contradictory observations.

The owner expanded the scope to an editable Mezan 2 fee table, including per-event transfer and refund charges, as the source for operational balance estimates. Explicit source-mode activation is implemented. Provider estimates and affected readers use the same saved policies, with actual statement fees taking precedence. Additional executed-refund charges and cumulative refund rounding are integrated. Transfer fees use the actual recorded settlement amount, because historical movement defaults cannot distinguish an omitted fee from an explicit zero.

Final BNPL completeness checks passed 34 tests with isolated Mongo, including twelve API and generation boundary cases and the existing generation, matching and provider suites. Weekly and reconciliation views retain known amounts while exposing incomplete fees. Registration preview reports a review error, and invoice generation returns incomplete periods before their write path. CI explicitly includes the new fee, completeness and UI regressions.

The extended Order Engine route fixture requires a database stub with `unified_orders`; its first three setup cases stop before route assertions. The fixture and its relevant router dependencies match the base commit. Service, refresh, mapper, repository and model consumers passed the expanded suite.

Independent arithmetic replay passed all supplied authentic BNPL events and the separately retrieved stored settlement rows. The source review identified unavailable current installment-plan evidence, incomplete provider pricing evidence and a rejected provider authentication probe. Operational adoption dates and these coverage gaps require explicit disclosure in the completion report. Private analysis records preserve the source provenance and arithmetic comparisons.

## Release handoff

PR #1753 merged as `f1dfb844393bf86b8a82d1a1fb6eeeadff8f694f`, preserving source A `01612f3040b50155017be9d2c91defb47c814e4b` and intent-only B `51d7c01a75998e6432323b8bd153cbe33fd8f0a5`. All twenty-five applicable workflows passed. Clean-clone deployment rehearsals passed with host Node 20 and 24 and on the Emergent server. Publication started on 5 November at 00:07 Riyadh time after the preceding release lease closed and the new prepare/prepublish checks passed.

## Print summary follow-up

The owner requested compact printed financial summaries after observing individual delivery cards across many pages. The follow-up is based on the merged release and targets the existing report print button. It removes the per-order print-card template, hides detailed review sections and verbose diagnostics in print, preserves concise completeness warnings, and allows summary tables to flow across pages with repeated column headers and a single final total.

The complete summary rows are expanded before the print clone. Normal screen pagination and the selected page are restored after printing or a print error. Three focused regressions failed on the previous source. The completed patch passed thirty-seven tests across four suites and an independent no-cache repeat. Browser print-preview acceptance remains pending.

## Pending work

- Verify the current publication identity and close its matching release lease.
- Save supported merchant fee policies through the owner UI and verify live reconciliation and repeat-run stability.
- Freeze, test and publish the print follow-up with a new v5 A/B contract, then verify the report print preview.

Issue #1006 reached its comment capacity. The release PRs and this status document preserve continuation. Private merchant evidence and fee configuration remain outside Git.
