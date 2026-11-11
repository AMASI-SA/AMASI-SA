# SERVICE-OPTION-COST-AUTHORITY-20261111 — completed publication evidence

## Current checkpoint: published and verified

Recorded 2026-11-11 at 17:08 UTC. This section supersedes the archived pre-release
status below. Repository: AMASI-SA/AMASI-SA. Implementation PR:
https://github.com/AMASI-SA/AMASI-SA/pull/1879 (merged).
The frozen implementation branch remains
`codex/service-option-cost-authority-20261111`.
This documentation-only evidence lives separately on
`codex/service-option-cost-authority-20261111-evidence`; it is not a new
production candidate and must not be merged or published as one.

### Owner authorization and data boundary

The owner explicitly authorized publication to expose the hidden direct name
cost so they can remove it themselves. Publish and verify only. No production
cost write, delete, historical backfill, cleanup, or repricing was performed.
The direct name cost 10 and independent embroidery service 8 remain intact.

### Immutable release chain

- Base J: `77e687e6964254617b4aa9eb0e3164cc968cff3b`.
- Source A: `19441a985ec99c3aa49b81f1d4b47472c3c86af8`.
- Source A tree: `c5d66ca693e59403753fed180867e218df716e6c`.
- Intent-only B: `13f1cd26f0ead560b208127d8a5c9c7ceececcff`.
- B tree: `f95ddb857df15be7ae10aeb9b5135f1c06862716`.
- Production merge M: `2d6d74806ab32333a1de1cfc8b8c73c3e434a851`.
  M preserves A ancestry and has exactly B's tree.
- Runtime release:
  `rg5-57779cd9ec0e5963f37e4922ec895e1858879cefd4a6d94d6d024f1d40f7f1ec`.
- Intent: 1,264,209 bytes; SHA256
  `66a26b55b723f08412a6f2d2c2aaaf1d3931b24842bb538ad65a17624ca12037`.
- Frontend artifact tree:
  `ced2c43f29fe89f8c1002f5373793395a1e90a362a291b70b0d0405e4bce92a1`.

### Verified release gates

Source A Release Readiness run 43054461213 succeeded; artifact 13871805241
archive SHA256 is
`b302658c99e1d8c82c22cfd4ae21ef2032671c0b20e7e45864a2f430c5c9bd8b`.
All applicable reviewed B workflows succeeded. Release Readiness run
43055317705 ran both actual clean-clone adapter rehearsals successfully:
Node 24 job 130643771858 and Node 20 job 130643771957. Conditional TikTok
candidate and PR manual-redeploy handoff jobs were skipped as designed.
The complete local reviewed-intent validator also exited 0.

The prior foreign `support-performance-recovery-20261111` lease closed through
its owner's verification before shared /app was changed. A fresh status was
active:false. Shared /app was safely fast-forwarded from J to M, retaining
untracked files and the user's unsaved editor buffer. No reset --hard was used.

The existing /app/frontend `yarn build` dispatcher ran the deployment adapter,
with reviewed public origin https://mezansalla.com and bytecode disabled.
It printed Done in 63.21s, emitted source A and the expected release ID, and its
actual shell exit status was 0. Full local output was retained in
/tmp/service-option-cost-1879-adapter.log; durable summaries are in PR1879.

Prepare exited 0 with actor `codex-service-option-cost-1879`,
prepared_at `2026-11-11T16:40:00.265730+00:00`, source A, deployment M, and
the expected runtime ID. Prepublish exited 0, explicitly ready_to_publish=true.
One authorized Publish changes action was submitted. No subsequent source,
guard, release-intent, or materialization change was made.

The current Emergent UI displayed a new green **Deployment finished** event at
Nov 11, 07:51 PM (16:51 UTC), and new published version `5e94faf`; the prior
`eb8949e` moved to the previous version. This is the UI's observed wording;
do not claim that the literal text Deployment Succeeded was displayed.
The UI exposes runtime logs, not a complete Cloud Build transcript.
No paid agent, credit purchase, or subscription change was used.

A fresh public health read showed source A, the intended runtime ID,
ok=true, verified_identity_available=true, frontend_build_verified=true, and
boot_started_at `2026-11-11T16:51:11.247105+00:00`.

### Live functional verification completed (read-only)

A fresh product editor load for AMS10966 / mpv2_1500919302 now renders:
- Name condition: عند ورود الخيار في الطلب (ربط سابق).
- Direct amount: مبلغ مباشر · +10.00 ر.س.
- An enabled تعديل التكلفة action.
- Its dialog contains direct-cost value 10 and enabled حذف, إلغاء, حفظ buttons.
- The independent embroidery yes choice still displays تطريز الاسم · +8.00 ر.س.

The dialog was opened for inspection only. Neither delete nor save was clicked.
Product page:
https://mezansalla.com/products-v2?product=mpv2_1500919302&focus=cost.
A local browser screenshot records the cost dialog; no customer data or
credentials were extracted or persisted.

### Final production verification

The unmodified command
`PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py verify --url https://mezansalla.com`
ran in /app Terminal 1 from about 16:53 UTC and completed by 17:08 UTC.
The wrapper printed **VERIFY_OK**, which is emitted only for the actual
guard exit status 0. It then ran guard status and displayed **active:false**.
The successful guard completed its required three full consecutive probes,
binding source A to deployment M and the exact intended runtime release.

Observed completion evidence:
- Frontend artifact tree:
  `ced2c43f29fe89f8c1002f5373793395a1e90a362a291b70b0d0405e4bce92a1`.
- `frontend_checked_requests_count`: **576 per probe**.
- Three complete public checks: **1,728 frontend requests**, plus the
  health identity checks.
- Boot identity:
  `2026-11-11T16:51:11.247105+00:00`.
  The boot observation list contained this same value.
- The verified v5 code requires intended identity, backend/control manifests,
  critical hashes, frontend proof, public file bytes, both canonical and
  cache-busted build-meta JSON bytes/MIME, cache policy and service-worker checks.
  No validator or verification gate was changed or skipped.
- Matching release lease closed automatically; the subsequent status was
  `{"active": false}`.

Full operational output is retained in
/tmp/service-option-cost-1879-verify.json on the authenticated project host.
The material outcome and identity are preserved here and in PR1879, rather
than depending on that temporary log.

### Final handoff and next action

Publication and read-only functional verification are complete. Do not republish
this task. The product page is ready for the owner to review the visible legacy
direct name binding and choose deletion:
تكاليف الخيارات والحقول → الاسم → عند ورود الخيار في الطلب (ربط سابق) →
تعديل التكلفة → حذف.

The cost dialog still shows 10 and its delete control. Embroidery 8 remains
independent. No save or delete was clicked by this task. Actual production
save/delete acceptance is deliberately outside the requested publication-only
boundary; the owner performs the next data action.

Production changed: **yes, application code only**, evidenced by the new release
identity, successful full public verification, and live fixed editor.
Production cost bindings changed by this task: **no**.

Previous evidence-only checkpoint:
`d7e23dc063da62849215e6c5d5d6bfd490f961e2`.
The final evidence commit is recorded with its full SHA in PR1879. This evidence
branch is documentation only and must not be merged or published as another
release. The frozen A/B pair, production M and shared /app remain unchanged.

Issue #1006 is at its comment limit; PR1879 and this evidence branch are the
durable continuation ledger. No blocker remains for the requested publication.
Historical frozen report cells are not backfilled; the limitations in the
archived implementation evidence still apply.

---

## Archived pre-release status (historical; superseded above)


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
