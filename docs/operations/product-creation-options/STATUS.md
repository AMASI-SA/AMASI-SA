# PRODUCT-CREATION-OPTIONS — production integration checkpoint

Scope: create ready-made products or shippable made-to-order services from Mezan,
with text/single-choice customer fields, help descriptions, editable value rows,
the one-to-ten-year size preset, and conditional visibility (gift-card example).

Repository: AMASI-SA/AMASI-SA. Remote task branch:
`codex/product-creation-types-options`. PR: #1371.
The exact checkpoint commit is recorded in Issue #1006 and the PR head.
Reviewed production base: `2196670da3a5cbf47ac633548a344529dd5b41f7`.
Previous main-based implementation: `ae3ef8054a3901717117087335b443f92b5ccfa9`.

The previous PR targeted stale main. This checkpoint integrates the feature
onto the actual production source and preserves the current role-assignment
contract, temporary operation/inventory freeze, Vite/release tooling and all
unrelated production changes. No release-control source or tracked intent is
changed. New conditional products remain hidden and unavailable until Salla
read-back confirms the actual parent option/value references.

Validation on the production integration:

- Focused creation rules/workflow: 39 passed, exit 0.
- React/jsdom interactions: passed, exit 0; ten sizes, row editing/removal,
  duplicate validation, preserved media, gift text visibility/requiredness,
  descriptions, reopening a saved draft and frozen operation defaults.
- Full Products V2 local collection stopped because the local test environment
  lacks the existing CI-pinned OpenAI SDK; no source regression was inferred.
  The unchanged production CI dependency list supplies that dependency.
- Python compile and `git diff --check`: passed, exit 0.
- Required checks and release readiness must be re-evaluated on this checkpoint;
  old main-based CI is not production-integration evidence.

Release/deployment: not merged, no new reviewed intent, no lease, no publish,
no restart, no live Salla acceptance test. Production runtime changed: no.
Emergent sign-in is incomplete; no `/app` execution access is established.
The separate inventory release/archive task and its files/intent are preserved.

Next safe actions: inspect exact production-based CI; establish an authenticated
Emergent session and read fresh guard status (`active: false` required); generate
the source-bound reviewed v5 intent, run its required clean-clone/package/runtime
checks, then merge without squash/rebase, rehearse and publish through the
existing guarded project. Verify the exact deployed identity before exercising
the actual product fields. Never restore old intent/lease or replace production
with the stale main tree.
