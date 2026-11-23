# Conditional current-product bundle preparation

## State

### Final source verification and blocked publication — 2026-11-23

This is a documentation-only continuation record on
`codex/conditional-bundle-release-evidence-20261123`. It is **not a deployment
input**. Keep the accepted source on
`codex/conditional-bundle-release-20261123` immutable at
`9b0bb3161aee3e7ad5f8631d8337c862d1c63eaf` (tree
`39633d984f5b9af981d9f2be4f201331b13840eb`). Its direct reviewed Production
base remains `76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43`.
Current source PR: https://github.com/AMASI-SA/AMASI-SA/pull/2088.

Exact-source CI completed with **24 successful checks, 6 expected conditional
skips, and no running, failed, cancelled, or timed-out check**. The root reread
the exact commit's check-run collection after the independent reviewer reported
completion at 16:06:28 UTC. Both previously red jobs now pass:

- Operational Balance Backend, job `137816877074`, completed 16:05:25 UTC:
  6,615 passed plus 90 subtests; subsequent selections passed 106 tests and
  16 tests plus 18 subtests.
- Stock eligibility/reservation/consumption on isolated Mongo, job
  `137816875995`, completed 16:05:40 UTC: 5,531 passed plus 97 subtests.

The independent reviewer read the completed job logs. These groups overlap
and are not a unique combined test total. Source-stage adapter and redeploy
skips do not establish release acceptance. No source/intent pair B, actual-host
release rehearsal, task-owned lease, merge, publish, or production verification
has occurred for this task. The 393/5 local backend and 45-test frontend results
below remain valid; no source changed after that accepted source commit.

The last completed actual `/app` guard reads still reported the other task's
active lease owned by `codex-tabby-weekly-fees-2086-20261123`. A later status
read did not return a new result; no inactive state was observed or inferred.
The owner must complete and close that lease. Do not abort it, reuse it, or run
its final verification on the owner's behalf. The user's merge/publication
authorization persists and does not require another permission request.

Live read-only inspection also established that the product is still ordinary
and already has a conditional direct-cost binding for the second garment.
When eventual bundle activation multiplies the base by physical quantity, that
binding must be handled explicitly to avoid charging the second garment twice.
No live product, price, cost binding, receipt, order, or invoice was saved.

#### Required operational work before declaring the original goal complete

The existing supplier-receiving workflow uses physical preparation pieces and
finalized supplier invoices. It is different from native purchase invoices,
inventory receipts and bundle lot allocation. A native purchase-requirement
reference alone cannot repair the existing receiving screen.

Keep one commercial parent unit with two immutable bundle leaf identities.
Adding ordinary unit 2 under commercial quantity 1 is rejected by supplier
cost reporting, stock unit selection and readiness coverage. Giving both
pieces ordinary unit 1 instead violates duplicate product-charge protection.
Do not relax those invariants or modify the commercial sale quantity.

A bounded operational implementation still needs:

1. A supplier-specific leaf bridge: materialize physical leaf pieces with
   frozen per-piece specs, supplier/custody provenance, independent physical
   identities and an explicit commercial-parent mapping.
2. A verified finalized-supplier-invoice receipt channel into bundle actual
   cost and readiness. Reuse the existing payable once; do not create another
   purchase invoice or fictitious inventory receipt. Parent readiness must
   require both covered leaves. Merely setting leaf state to `ready` would
   incorrectly use catalog cost as actual under the current contract.
3. An explicit audited conversion for an existing dispatched ordinary piece,
   allowed only before any receipt, finalized supplier invoice or consumption.
   Preserve old dispatch/custody history and map/retire the parent identity
   rather than deleting its intake snapshot. Existing ordinary intake capture,
   bundle amendment and bundle refresh do not provide this conversion.
4. Transactional and public-route tests for preview/commit, duplicate/replay,
   stale and cross-owner evidence, partial versus complete leaf receipt,
   quantity/cost aggregation, concurrency and exact assembly coverage.

Existing scan removal/session cancellation can reconcile an unfinalized draft
under its own actor/event fences. They are not a reversal of a closed invoice.
No migration or receipt was performed during this analysis.

**Next safe action:** re-read the original shared guard and current Production
base after the owner closes the other lease. Follow the original v5 governed
source/intent/rehearsal/merge/publish/verify process for the accepted source.
Resolve and test the operational adapter/conversion before product activation
or claiming the existing invoice now contains two garments. Evidence branches
must never be used as deployment input. Production changed **by this task: no**.

### Resumption on 2026-11-23

The owner explicitly authorized merge and deployment. Browser access is now
working. A fresh read of the actual shared `/app` guard returned `active: true`
for actor `codex-tabby-weekly-fees-2086-20261123`, source
`1725672eeccb1272f183c376b9267e363a028325`, deployment
`76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43`. That other task's lease must be
closed by its owner. This task has not merged, created a lease, rehearsed a
release, published, or changed live business configuration.

The refreshed source preparation branch is
`codex/conditional-bundle-release-20261123`, directly based on current
Production `76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43`. The original PR #2071
remains a recoverable source checkpoint. A local merge experiment was not
published: its full-history ancestry touched the release intent, so the new
branch uses only the bounded source patch on the current Production base.

Both old red CI jobs were traced to the same exact-projection test fixture:
`test_every_owned_authority_phase_drains_motor_before_lease_release[estimate_catalog]`.
The loader intentionally added `skus` and `variants_count`, but the fixture
still expected the older projection and never intercepted its cursor. The
failure was reproduced, then corrected by adding those two expected fields.
Exact matching, unrelated-read exclusions, and cancellation assertions remain
unchanged. Fresh focused result: 33 passed. The two prior red CI gates have not
yet been demonstrated passing on the new remote source.

The new slot backend/UI and physical-quantity regression files are now included
in the G47 and sold-product-cost CI selections. Fresh root integrated check on
the current Production source plus the bounded patch: **393 passed, 5 skipped**,
three dependency/existing deprecation warnings. The skips retain the same
isolated-Mongo prerequisite described below. No lockfile or runtime dependency
manifest was changed.

Fresh frontend verification with the governed Node 22.23.2/Yarn 1.22.22
toolchain: **45 passed in 2 suites**, exit 0. Command:
`CI=true python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath src/components/products/CurrentProductSlots.test.jsx src/components/products/BundleEditor.test.jsx`.
Both changed workflow YAML files parse, and `git diff --check` passes. The
source patch copied onto the direct current-Production base was verified
byte-identical to the tested files; only this evidence text subsequently changed.

A fresh public health read still reported source
`aec203382b387c3812509084e77c2922aa860877`, with verified identity, critical
hashes, and frontend build all true. It did not report the active other-task
lease's source. No completion or release of that other task's lease was inferred.

Live read-only inspection confirmed that the requested existing order is
already in progress and its one ordinary piece has already been dispatched to
the supplier. Catalog changes alone cannot convert that piece. No order or
customer identifiers are retained in this repository record.

An independent executable receipt-contract check found additional activation
blockers: native purchase configuration canonicalizes size/color names and
rejects ambiguous names, while frozen per-slot specifications use stable option
IDs. A native generated configuration cannot match those frozen leaf specs;
the actual bundle receipt route correctly rejects it with
`bundle_purchase_receipt_customization_mismatch`. A pair with duplicate
canonical size/color labels is rejected earlier as
`purchase_configuration_option_names_ambiguous`. Do not weaken either check or
rewrite frozen plans to force a match. These are not repaired by this bounded
UI/reporting patch. A compatible, server-derived frozen-requirement purchase
path and an audited existing-piece conversion remain separate work before
activating this product's complete operational bundle workflow.

Next safe action: finish exact-source CI and independent review; re-read the
shared original guard before any release rehearsal or production merge. When
its owner has closed the other lease, create the governed source/intent pair
and complete the normal release process. Keep product activation and the
existing order unchanged until the documented receipt/conversion gaps are
resolved. Deployment authorization persists; no repeated permission is needed.

### Original verified checkpoint

Verified source preparation on `codex/conditional-hoodie-bundle-20261122`, based on
`9b4fb716b430c32073b5ea7abaeaff62646d9633` from
`hotfix/prod-snap-meta-final`.

Production and business records have not been changed. No release lease,
merge, publication, order conversion, catalog write, supplier receipt, or
invoice change has been performed by this task.

## Recoverable source checkpoint

- Repository: `AMASI-SA/AMASI-SA`.
- Remote branch: `codex/conditional-hoodie-bundle-20261122`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/2071.
- Source commit: `c19f8daf312196d77957e389d96c23f8630487c1`.
- Verified source tree: `fbf2a2747cd4a671aab968079606ef3ed115f5a4`.

The remote commit was fetched and its tree verified identical to the local
tested tree. This status update is documentation-only after that source
checkpoint. Read the PR's current head for the latest handoff commit; do not
mistake it for a governed release-intent commit. The repository continuation
issue (#1006) is at its comment limit, so the Draft PR and this document retain
the task-specific handoff. No issue comment was sent.

Review outcome: no concrete blocker found in the bounded source scope after
the two reproduced defects were fixed. Operational completion remains blocked
as described below. GitHub CI and live acceptance are not claimed passed.

## Requested behavior

One commercial product offers either of two individual garments or a pair.
The selected customer option determines one or two physical garments. Each
physical garment keeps its own size and color choices, even when the store
catalog models those fields as form choices rather than inventory variants.
Supplier unit cost is a per-garment cost; a pair multiplies it by two without
adding a third parent cost or another sale. Shared packaging remains once per
commercial bundle; explicitly per-unit components follow the physical count.

All automated examples are synthetic. Customer/order identifiers and live
financial data are deliberately absent from this repository record.

## Source scope

- `backend/bundle_catalog_v2.py`: optional `selection_option_ids` on a
  current-product slot, preserving the chosen specifications of that piece.
- `frontend/src/components/products/CurrentProductSlots.jsx`: per-piece choice
  field mapping without requiring inventory variants.
- `backend/current_bundle_reporting_estimate.py`: bounded current-catalog
  estimates for mutually exclusive simple current-product quantity rules,
  including display-only slots and explicit component quantity scopes.
- Focused regression tests for both behaviors.

The estimator remains an expected cost from the current catalog. It does not
become a historical invoice or receipt, replace frozen cost authority, create
stock, or execute supplier work. Unsupported variant/override/deduplication
semantics remain unresolved.

## Verification checkpoints

Baseline regressions were demonstrated before edits:

- Per-piece choice mapping: rejected with `bundle_invalid_slot`.
- Physical-repeat reporting suite: 19 failed and 17 passed; intended failures
  were `current_bundle_extra_parent_unresolved` and
  `current_bundle_advanced_recipe_unresolved`.

Independent review identified two defects in the initial slot implementation:
ambiguous joined labels and proven-hidden mapped choices. Both were reproduced
and corrected. Fresh independent review confirmed that a hidden mapped field
does not block its active piece, a supplied hidden answer is rejected, and
ambiguous normalized labels are rejected before any recipe is persisted.

Final backend verification (exit 0):

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q \
  backend/tests/test_bundle_slot_choices.py \
  backend/tests/test_bundle_catalog_v2.py \
  backend/tests/test_current_bundle_physical_quantity.py \
  backend/tests/test_current_bundle_cost_details.py \
  backend/tests/test_order_cost_authority.py \
  backend/tests/test_dashboard_bundle_cost_authority.py \
  backend/tests/test_bundle_reporting_financial.py \
  backend/tests/test_bundle_reporting_cost_policy.py \
  backend/tests/test_sold_bundles_v2.py --tb=short -rs
```

Result: **360 passed, 5 skipped**, two existing Pydantic deprecation warnings.
The five skipped cases require an isolated loopback MongoDB/replica-set fixture;
they were not run against Production. An earlier integrated run encountered
three missing-local-dependency errors (`qrcode`); installing the declared local
runtime dependencies and rerunning the same suite produced the result above.

Final frontend verification (from `frontend`, exit 0):

```bash
CI=true node node_modules/react-scripts/bin/react-scripts.js test \
  --watchAll=false --runInBand --resolver ./scripts/jest-package-exports.cjs \
  --runTestsByPath src/components/products/CurrentProductSlots.test.jsx \
  src/components/products/BundleEditor.test.jsx
```

Result: **45 passed in 2 suites**. `git diff --check` passed. Independent review
also reran the focused slot/estimate suite: **84 passed**. These overlapping
counts are not additive. No governed release build, release rehearsal, CI
acceptance, live browser acceptance, or existing-order conversion is claimed.

The production Git branch was reread after verification and remains at the
base SHA recorded above. Only the three source files, their three focused test
files, and this status document are changed. Unrelated work and the tracked
release intent are untouched.

## Live access and remaining work

Browser inventory and tab operations currently time out, so authenticated live
Mezan product/order state has not been read or changed. A public health read
returned source `23e9514b9e8a33bffb2363bb1d3afd792355ab2c`; fetching that commit
and comparing the affected bundle, receipt, and slot files against the source
base showed no differences. This is source comparison evidence, not a
deployment or business acceptance result.

Before any live setup:

1. Read the current product, cost profile, supplier policy, bundle revision,
   inherited component scopes, and exact order line lifecycle.
2. Preserve existing variant costs and notes when updating the approved base
   unit cost. The existing costs PUT replaces those fields if omitted.
3. Configure three mutually exclusive `current_product` rules (first item,
   second item, pair) with quantities 1, 1, and 2. Do not add an unconditional
   extra parent garment. Map each piece's own choice field IDs.
4. Preview individual selections and the pair, with commercial quantities one
   and two. Verify specs, unit/total cost, supplier, and component scopes.
5. Inspect whether the existing order was already frozen as a normal product.
   Saving a catalog bundle does not change an existing null intake snapshot.
6. Do not change commercial quantity, write a pair total as a unit cost, delete
   snapshots, duplicate supplier invoices, or replay a receipt as a workaround.

The existing ordinary supplier-receiving flow counts actual piece identities.
Bundle parents use the bundle requirements/receipt route and are excluded from
ordinary preparation export. A tested bridge or an explicit audited conversion
is still required if the existing order must continue through that ordinary
supplier screen. That work depends on the actual preparation/receipt state and
is not implemented or claimed complete by these source changes.

Do not publish this preparation as if it completes the live request. Resume
with a fresh branch/source check, the recorded tests, authenticated read-only
state inspection, and the repository's normal release protocol when deployment
is separately authorized. Never clear another task's release lease.
