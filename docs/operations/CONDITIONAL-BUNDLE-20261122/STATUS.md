# Conditional current-product bundle preparation

## State

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
