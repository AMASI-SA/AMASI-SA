# SERVICE-OPTION-COST-AUTHORITY-20261111

Repository: AMASI-SA/AMASI-SA.
Task branch: `codex/service-option-cost-authority-20261111`.
Original source base: `f439bed54579fafb79a8abcb43080c8150cb5063`.
Release integration base J: `77e687e6964254617b4aa9eb0e3164cc968cff3b`.
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1879.
Initial remote checkpoint: `3e72df4ededd6fa814e4e4371098b9f7cbdab0c9`.
Verified pre-integration source: `ae83670a2e4e022e6008b975717e1e7f039dfddd`.
No production release or intentional production cost/configuration write yet.

## Current owner authorization

The owner explicitly authorized publishing the fix, then clarified that the
purpose is to make the hidden value visible so they can delete it themselves.
Publish and verify visibility only. Do not edit or delete any cost binding,
backfill historical cells, reprice orders, or perform production data cleanup.
The current direct name cost of 10 and independent embroidery service of 8
must remain intact for the owner to review.

## Observed incident and evidence boundary

The owner reported an extra direct name-option cost in the sold-products report.
Read-only UI inspection confirmed that the name field says no additional cost,
while a separate embroidery choice has a service cost. The report includes both.
Do not infer who created the direct cost or that Salla's selling surcharge was
imported as a cost. The code reads direct costs from an explicit saved binding;
historical reporting snapshots can also retain a previous binding's cost.

The source has a reproducible visibility defect: the editor generates only
`filled` for text fields while the API and cost engine support `__option__` too.
Bindings for removed options/values are also omitted by the old editor. The API
already returns those bindings. A separate display-count defect omits resource
identity from ordinary catalog cost cells.

Authenticated, ordinary browser GET to the application's product option-cost
endpoint confirmed the exact defect for AMS10966 / product mpv2_1500919302:
the name field has a saved direct amount of 10 under the legacy `__option__`
key, while the separate embroidery yes choice resolves to a service amount of
8. The old editor hides the former and displays the latter. The name record was
created and last updated at 2026-10-26T21:28:03.814000 UTC, but has no actor or
source metadata. Its human creator remains unknown. No credential was extracted.

The report currently sums product 35 + bag 5.50 + name 10 + embroidery 8 =
58.50. Removing only the old name binding would leave 48.50 under the current
catalog costs, but that data action is reserved for the owner. The one order
returned by the normal Orders UI for this SKU on 11 November had no persisted
cost snapshot according to its read-only cost-snapshot endpoint. This sample
does not establish the historical population or authorize repricing. Exact
binding evidence is retained in the task PR.

## Implementation scope

- Display every persisted option-cost binding, preserving exact stored keys and
  the existing distinction between an option being present and a field being
  filled. Expose removed options/values for review and exact-key removal.
- Prevent new text-field aliases from creating a second hidden cost; preserve
  existing legacy records and allow their explicit review.
- Record future option-binding changes with bounded before/after metadata and
  the actual mobile actor when the authenticated principal is merchant-scoped.
- Carry resource identity to newly calculated report columns without merging
  independent charges. Older frozen cells without identity are not backfilled.
- Keep recorded historical costs unchanged. This is not an automatic data
  cleanup or a correction of historical financial amounts.

## Verified source checkpoint

- Report resource-identity regression failed before the producer fix and passed
  after it. A separate financial fixture proves that a direct name cost and an
  embroidery service remain independent and Salla sale prices do not become cost.
- Backend final integrated run: 114 passed, exit 0. From `backend`, using the
  existing local `courier-test-env/bin/python`, `PYTHONDONTWRITEBYTECODE=1`,
  `pytest --noconftest -p no:cacheprovider
  -o asyncio_default_fixture_loop_scope=function -q` on:
  `test_product_option_cost_alias_guard.py`, `test_order_option_cost_snapshots.py`,
  `test_product_option_shared_cost_engine.py`, `test_product_field_cost_support.py`,
  `test_report_option_identity.py`, `test_order_cost_authority.py`,
  `test_authority_review_regressions.py`, `test_sold_product_cost_details.py`,
  `test_snapchat_profitability_cache_scope_v1.py`
  (all under `tests/`). Only an unrelated fitz deprecation warning.
- Frontend final integrated run: 47 passed in four suites, exit 0:
  `ProductOptionCostEditor.test.jsx`, `SoldProducts.test.jsx`,
  `mezanProductsV2.test.js`, `mezanComponentCatalog.test.js`. Used the existing
  React Scripts/Jest runtime from the older local checkout, with rootDir pointing
  at this task, an in-memory configuration, runInBand and no cache. No dependency
  or lockfile changes. CI uses the repository's ordinary Yarn test command.
- Editor regression had 12 failing cases before the implementation. The API
  alias guard and mobile-actor audit regression also failed before their fixes.
- Independent read-only review found an incorrect mobile actor attribution;
  it was fixed and the 26 new API tests were independently rerun successfully.
  Independent editor/service tests: 30 passed. No remaining blocking finding in
  the editor/write guard within the stated scope.
- `git diff --check`: passed. New editor and API suites are wired into the
  existing sold-product-cost-details workflow.

## CI follow-up

Source `8b2e0eb812508b72cc4e98912bfcdb6eaa10297b` passed 11 workflows,
including Products V2, Sold product cost details and Release Readiness. G47
run `43036819447`, job `130577701890`, exposed one test-double incompatibility:
the hand-written collection in the cache-revision test lacked the newly used
atomic Mongo methods. Its first group passed 327 tests and 127 subtests; its
cost-acceptance group passed 435 tests with this one failure.

Independent comparison reproduced that failure on the patch and passed the
same test with the original base route loaded in memory. This was caused by
the patch's storage API change, not an unrelated baseline failure.

The affected test now uses the existing AsyncMongoMockClient dependency; its
original revision assertions (1 after save, 2 after delete, still 2 on repeated
delete) are unchanged. No production implementation was weakened. Independent
rerun of the complete cache test file plus alias-guard suite: 29 passed.

All 12 workflows on `ae83670a2e4e022e6008b975717e1e7f039dfddd` succeeded,
including G47 (43040435853), sold product cost details (43040435909), Products
V2 (43040435742), CodeQL (43040435768), and Release Readiness (43040435999).
Release Readiness treated this as source candidate A; its clean-clone adapter
rehearsal was skipped. That run is not evidence of a deployable reviewed B.

## Release integration and remaining gates

Production advanced first to `3f60c1a08263ff1eb2412c0f9224467a198ac278`,
then to `77e687e6964254617b4aa9eb0e3164cc968cff3b` through reviewed PR1881.
The later release changes 48 files for Qoyod recovery and manual campaign
monitoring; none overlap this task's nine files. Integration preserves every
production change and the current production intent. The new source A has J as
its single parent and applies only this task's nine-file patch. A merge-shaped
candidate is unsuitable because its full intent-path history can include the
previous release transition even when the final intent bytes equal J.

The earlier 635b57b candidate was correctly rejected for that history condition.
The single-parent 8adacbc candidate passed local transition validation, but its
PR readiness run then detected that production had advanced to 77e687e. Neither
candidate had an intent B frozen. The source A and intent-only B must be newly
built on the current base. Do not change release guards or reuse an older intent.

Fresh focused checks on the first integrated tree passed 43 backend tests and
16 editor tests. Secure VS Code sign-in then succeeded. The shared Release Guard
reported active false, and `/app` was on 77e687e with no tracked changes. Its
untracked files and the owner's unsaved editor buffer are preserved. AGENTS.md
matches the reviewed repository instructions.

Before any `/app` change or release rehearsal, inspect the shared Release Guard
and continue only if active is false. Do not clear or reuse another owner's
lease. Follow AGENTS.md's source A / intent-only B contract, actual clean-clone
rehearsal, prepare, prepublish, one publish, explicit newer Deployment Succeeded,
and three-probe verification. Keep A/B ancestry; do not squash or rebase after
freezing. CI by itself does not publish the application.

## Remaining evidence and limits

- No production binding or historical snapshot has been changed. Product-specific
  deletion is reserved for the owner after the fixed editor is published.
- Storage verification uses `mongomock_motor`, with deterministic race hooks;
  real Mongo acceptance and the deployed UI have not been verified.
- The audit insert is separate from the binding mutation, following neighboring
  routes. Revision invalidation precedes audit. An audit outage can therefore
  leave a saved mutation with an error response and no audit event; this is not
  claimed to be an atomic audit transaction.
- Old frozen cells without resource identity can still overcount alternative
  bindings. Their amounts remain frozen. Do not claim that every historical
  display count is repaired or infer old resource identity from current prices.
- No governed release build, intent freeze, merge, lease or deployment occurred.

## Continuation

Issue #1006 has reached GitHub's comment limit, as recorded by prior tasks.
Use this document and the task PR for exact remote SHAs, verification and handoff.
Preserve the unrelated active checkout at `/workspace/scratch/e2ab5db77417/mezan`
and all other task worktrees. Work only in this task's isolated checkout.

Next: checkpoint the integrated source A remotely, verify the focused regression
checks, inspect `/app` Release Guard status through the project's VS Code terminal,
and complete the reviewed v5 build/intent/CI handoff before merge and publish.
The project terminal is authenticated; do not request a database connection
string for this release. After deployment,
verify the live editor exposes the saved name 10 with exact edit/delete controls
and preserves embroidery 8. Leave both bindings unchanged. Record exact release
SHAs, CI and production verification in the PR without modifying frozen source A.
