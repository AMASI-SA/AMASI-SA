# PRODUCT-CREATION-OPTIONS — published and verified

Status (2026-10-15): production release PASS; deployed form configuration smoke PASS.
Live Salla product-write acceptance: NOT RUN in this release turn.

This is a documentation-only follow-up checkpoint on
`codex/product-options-published-status-1371`. Do not deploy this documentation
commit or merge it into the frozen release. Production remains exact D below;
this checkpoint does not modify A, B, D, the reviewed intent, or /app.
The full checkpoint SHA is recorded in Issue #1006.

## Scope and immutable source

Repository: AMASI-SA/AMASI-SA. PR #1371 merged without squash/rebase.
Implementation branch: `codex/product-creation-types-options`.
Reviewed intent retained on `codex/product-options-reviewed-e34fa4c9`.

- Previous reviewed production J: `2196670da3a5cbf47ac633548a344529dd5b41f7`.
- Product source A: `e34fa4c967d39a22a4e4a1ccb7a998698f194890`.
- Intent-only B: `02128b4ae790850a323f8ab30a978bf4c65cc4fa`.
- Production merge/deployment D: `231f5714257220cdfe192428275c2bca62e0bd2d`.
- B/D tree: `a2c05820c290962531cfc3ed82c2a59e4d1baac3`.
- Runtime release: `rg5-6d66523b3e6d00a51f6c74fe79bcf970c28fb02a9388501090bcf2f6da57c61b`.
- Frontend artifact: `d2dbe74dd4ca72214896feae83a929b88c736cf7e3f140b06aaa112344b7ea95`.

The implementation supports product/service types, explicit shippable-service
weight, text and single-choice customer fields, help descriptions, required
answers, editable value rows/counts, one-to-ten-year sizes and conditional
visibility. Gift text is configured to depend on the gift-card choice being
“نعم”. Conditional products are staged hidden and unavailable while actual
Salla option/value IDs and saved conditions are resolved/read back; uncertain
writes retain reconciliation state and prevent duplicate creation. Existing
role lookup, media/categories, temporary operation/inventory freeze, release
tooling and unrelated production source are preserved.

The implementation delta is the 11 files recorded in PR #1371: Products V2
workflow; backend creation routes and two tests; feature documentation and
this status file; creation panel, options editor, options preview, form tests,
and options serialization/validation helper. B adds only the reviewed intent.

## Verification

Exact-B checks: 17 success, 4 skipped, zero failure/pending.

- Products run `38796280221`: backend job `116633399771` reports
  278 passed (one warning); frontend job `116633400671` reports five Jest
  tests passed and Vite production build passed.
- Readiness `38796279932`: source reproducibility/intent binding and required
  Frontend build/Backend preflight passed. Clean-clone job `116633847956`
  passed Host Node 20 no-Git adapter, isolated package/identity checks and
  canonical/cache-busted JSON runtime routes.
- All four required checks and CodeQL Python/JavaScript/aggregate passed.
  The intermediate aggregate neutral warning resolved after Python completed.
- Clean-clone evidence artifact `12001324447`, SHA256
  `e5193b714bda7caece7fdf98e9204ca2a1897733eba6d31f1049b5f83f1a073e`,
  expires 2026-10-29T19:47:21Z.
- Earlier source-A local focused cases: 39 passed; React/jsdom interactions
  passed. These supplement, not replace, exact-B CI.
- Skips are candidate generation, manual deployment and unrelated Snapchat
  frontend/backend jobs; they are not passes.

Shared /app guard was inactive and tracked state clean before fast-forwarding
J to exact D with `git merge --ff-only`. No reset or unrelated cleanup.
Untracked `INTENT-1367.json.gz` and `deployer-agent-docs/` were preserved.

Local official adapter attempt 1 failed on two generated Python bytecode files.
One untracked cache directory containing those two regular files was moved
intact to `/tmp/product-options-bytecode-lcqx0r5b`; no source was changed.
Retry with `PYTHONDONTWRITEBYTECODE=1` and Python `-B` passed in 88.86 seconds,
exit 0, with exact reviewed identity/artifact and isolated package proof.
Original failure remains a failure in Issue #1006. Runtime logs expose no
complete Cloud Build transcript, so exact outer Cloud argv is not claimed.

## Publication and official production acceptance

Actor: `codex-product-options-1371-05c4b4df7c11`.
Prepare exit 0 at 2026-10-15T19:56:44.251806+00:00.
Prepublish returned ready_to_publish:true with exact A/D/release.
Exactly one “Yes, Re-publish” was submitted after that check.

Fresh platform transition: started Oct 15 10:58 PM Asia/Riyadh; green
“Deployment finished” Oct 15 11:06 PM and new Live snapshot `88c0fd7`.
Current UI uses “Deployment finished”/Live rather than the former literal
“Deployment Succeeded”; old snapshot `fcb925f` moved to history. Displayed
Publish numbers were renumbered, so snapshot/time are the stable evidence.

Official command (with bytecode suppression):
`python -B scripts/production_release_guard.py verify --url https://mezansalla.com`.

Result: exit 0, verified:true, three consecutive probes, exact A/D/release and
frontend artifact above. New boot: 2026-10-15T20:06:12.346183+00:00.
The proof records 40 frontend request variants per observation, including
/build-meta.json canonical/cache-busted and JSON/wildcard Accept requests.
Identity, complete source/critical hashes, artifact/proof, JSON bytes/MIME,
SPA and service-worker cache checks passed. Following status: active:false.
The guard closed its own lease; no manual clearing.

Verify JSON: `/tmp/product-options-231f5714-verify.json`, SHA256
`25b97662f2b65704ad3d7d63603930ee71efa10801848739d1c37e37d0a9e4ed`.
Last tracked /app status was empty. Production changed: YES, verified above.

## Deployed UI smoke

Signed-in production route:
https://mezansalla.com/products-v2?workspace=intake → “منتج جديد”.

Verified visible product/service selection and required service shipping
weight; ten editable age values from one through ten years; add a value
(count 11), validation of its blank value, remove it (count 10); help text and
required toggles; a gift choice with نعم / لا بدون كرت إهداء; text field
“كرت الإهداء” and description; condition controls pointing to the gift choice,
operator equals, value نعم, with incomplete-condition error clearing.

No test draft was saved, no approval or Salla write was performed, and SKU3203
was not duplicated or modified. Unsaved test form was closed afterward.
Live customer-preview interaction after saving, persistence and actual Salla
conditional acceptance remain unverified in production; their provider-fake
and frontend regression checks passed. Do not call this live Salla acceptance.
A screenshot of the deployed editor configuration was retained with the chat.

## Remaining / next safe action

Current publishing request is complete and guard inactive; do not publish again.
The user can now configure new products through the intake creation form.
For future provider acceptance, use a real authorized new product/SKU through
the existing draft/preview/approval/publish flow, then check actual saved Salla
fields and visibility. Do not attach a new creation draft to existing SKU3203.

Separate finding: broad runtime HTTP logs include sensitive authentication
query values. No values are recorded here, no logs were exported/shared and no
credential was used/rotated. Further broad log reads stopped. Logging hardening
is separate from this product-options patch; preserve the user's scope.
