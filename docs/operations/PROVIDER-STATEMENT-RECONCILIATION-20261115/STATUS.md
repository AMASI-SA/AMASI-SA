# Operational provider statement reconciliation

## Canonical release candidate lineage (2026-11-15)

Active candidate branch: `codex/provider-statement-release-20261115`.
The original task branch and Draft PR #1938 remain recoverable at
`aa2f54e17bf56061ce328c47a4c984ae275c1666`, tree
`65de3deef5e19d89163ebf6076e0291819c07553`; neither was force-updated or rebased.
The exact new candidate SHA/tree and its Draft PR are recorded in the new PR
handoff after preserving this tree.

Exact-head release-readiness CI at the original checkpoint failed before the
governed build, in `Classify reviewed B or source candidate A`:
https://github.com/AMASI-SA/AMASI-SA/actions/runs/43735505865/job/132877102556
The release contract's 57 tests passed, but full-history source/intent validation
found merge `6bb7895f5b71e1c8d9482a4ae70a843ff285af94` touching the intent path.
Although the final intent bytes equal production, v5 intentionally rejects that
candidate ancestry. No guard, workflow, intent or approval requirement is changed.

The repair stages the exact verified application source directly on reviewed
production `8f1b0ddb6408781e8677212bcb8cf2d51eab42f7`. Only this handoff document
differs from the preserved application tree. The intent bytes remain identical
to that production parent and the new source ancestry has no intent edits.
This is a new source candidate; no A/B intent for this feature was frozen or
rewritten. Fresh candidate classification must pass before proceeding to CI.

The prior exact-head operational frontend CI passed **883 tests across 54 suites**;
its backend preflight passed **156 tests**. Dedicated statement-page visual
acceptance remains blocked, and the other task's active production lease still
prevents shared-workspace or release-rehearsal work. No production change,
publication or live financial write was performed by this task.

Next: preserve the clean-lineage candidate, inspect its own exact-head CI, and
recheck the shared Release Guard before any authorized release work. Do not
merge or deploy the original merge-bearing feature checkpoint.

## Verified follow-up: saved statement binding (2026-11-15)

The owner asked to connect Tabby now so its fees appear in the provider table.
The verified feature checkpoint is
`33048c14f33a7bf06ff138ff54ff64a9fe9b9703`, tree
`912bb2f260d9a77528b7cec69d06053c595d9aaf`. Its exact-head CI completed with
37 successful and 6 intentionally skipped checks, no failures or pending checks:
4,929 backend tests, the 16 shipping tests with 18 subtests, 867 frontend tests
across 54 suites, build, preflight and repository browser/security checks passed.
The dedicated new-page visual acceptance remained blocked as documented below.

The latest reviewed production base was merged without conflicts and preserved
on the task branch as `6bb7895f5b71e1c8d9482a4ae70a843ff285af94`, tree
`d9f367578af85fb55de4a64dac1dadd583f07aac`. Its ordered parents are the feature
checkpoint above and `8f1b0ddb6408781e8677212bcb8cf2d51eab42f7`. This preserves
the concurrent Salla incident recovery and executive-profit changes. The combined
App routes were inspected; the only feature delta versus that production base
is the lazy ProviderStatements import and its protected route.

Read-only inspection of the live provider page and source proved two gaps:
legacy settlement originals are preserved but do not enter operational average
profiles, and available profile metadata is currently displayed only when an
average is applied. The follow-up now implements both:

- An explicit owner-only saved-file bridge lists bounded metadata and uses one
  selected original only after a separate action. It verifies owner, session,
  period, unique file/blob identity, matching metadata/computed hashes and sizes,
  safe XLSX structure and native provider identity. It reuses the existing
  immutable operational upload/CAS path; it never reruns the legacy importer.
- The provider table shows available source-average metadata independently of
  applied estimates: variable percentage before VAT, separately labeled source
  fixed mean in its native currency, tax evidence and source reference/date.
  Owner, provider, native-currency and merchant-account scope remain enforced.
  Existing financial amounts, completeness and application counts are unchanged
  by this presentation-only addition.
- Metadata list reads and selections never write. Explicit saved-use retries
  retain the same file/hash/period/request identity even after re-reading the
  metadata list. Missing principal and refund evidence remains explicit review.

Fresh verification on the integrated working tree, actual exit 0:

- Root combined backend gate: **304 passed in 26.08s**, including the existing
  intake, service/API, fees, pricing, subtotals, financial-position and affected
  payment/refund boundaries.
- Root frontend gate: **195 passed across 4 suites in 16.985s**, no skips.
  Command remains the four-suite command below, with the saved-file cases added.
- Backend owner: 49 service/API tests passed; independent source reviewer
  approved the exact frozen source/routes/tests after inspecting all new
  corruption, ownership, period, ZIP, replay and no-principal-write regressions.
- Fee-display owner: 113 affected tests passed. Independent frontend reviewer
  approved the frozen display delta and independently passed its 10 new source
  average cases. Root inspected the final UI delta and its retry correction.
- The real-component synthetic fixture build passed (4,630 modules); its updated
  browser harness passed syntax checks. The dedicated browser scenarios were
  **not run** because the previously documented browser restriction still
  applies. No screenshot or visual-acceptance pass is claimed.
- No application dependency or lockfile changed. No hardcoded merchant rate,
  customer workbook or live financial value enters the repository.

The shared production Release Guard was observed active for the separate
`codex-salla-incident-recovery-1942` operation. No shared `/app` change, release
rehearsal, lease takeover, live source refresh or live financial mutation has
been performed by this task. Production changed by this task: **no**.

Next safe action: preserve this verified source tree on the task branch, record
its exact remote SHA/tree and remaining review/CI result in Draft PR #1938, then
inspect the production release state. The previous merge checkpoint above is
the parent; it does not itself contain the saved-file implementation. No shared
workspace change or release rehearsal may start while another lease is active.

## Current integrated checkpoint (2026-11-15)

Parent checkpoint: `5e518936ad627cf3211ee748df4d47243b53fc83`, tree
`6c5c8383a9f5d0746876194abe7ea208c7a5855a`. The exact new remote SHA is recorded
in Draft PR https://github.com/AMASI-SA/AMASI-SA/pull/1938 after preserving this
tree. Production base is still `331b7917b3c01d064ee2a17affb4714f3a296331`.

The connected owner-only operational page, immutable intake, explicit matching,
fee projection, employee-evidence upgrade and native-refund alias lifecycle are
implemented. The independent review finding about a native refund alias after a
partial payout is fixed. A failing HTTP regression established the blocked
action before the fix; the canonical accepted refund proof is now checked before
fee eligibility. Linking changes only its metadata, while the overlapping fee
allocation remains under review. An independent source review approved this
bounded correction, with no remaining finding in its reviewed scope. This is
still a Draft pending the exact-head CI result and visual acceptance limitation.

Latest averaging contract is implemented: source variable commission before VAT
divided by the same sale principal, excluding fixed and payout charges. Preserve
the exact numerator/denominator; display is rounded separately. A valid known
tariff wins. Otherwise a uniquely configured pre-VAT fixed term or explicitly
estimated source per-sale fixed mean stays separate. VAT needs explicit source
or valid configured evidence; mixed or conflicting tax evidence stays incomplete.
Salla formats without separate fixed/variable columns do not invent a split.
Actual accepted evidence replaces only its covered estimate. Source profiles are
bounded and isolated by owner, provider, currency and merchant account.

All page reads remain demand-driven. History reads compact metadata and saved
review counts; receipts contain applied row IDs rather than copied review rows.
Missing-principal previews are bounded to 20 per action. Missing events require
explicit confirmation, current canonical proof and the existing engine's gates.
A single pending employee evidence record is upgraded in place. Later native
refund references produce an order-specific hold while other orders can refresh.

Fresh verification on the integrated source, actual exit 0:

- Root combined backend gate: **280 passed in 25.91s**, covering intake,
  reconciliation/API, fees, configured tariffs, subtotals, financial-position
  mapping and the affected existing pure refund/payment contracts.
- Service/API subset: **35 passed in 15.86s**, synthetic XLSX and
  `mongomock_motor`, including CAS/replay, source mutation, owner isolation,
  refund lifecycle, employee selection, metadata-only history and compact state.
- Fee/presentation subset: **137 passed in 4.41s**.
- Frontend: **179 passed across 4 suites in 27.314s**, no skipped tests.
  The isolated real-component fixture build also passed. Root verified all
  17 frontend and 7 service-file hashes against their freeze manifests.
- Original privately supplied workbook was parsed and its variable-only profile
  independently verified read-only. No merchant data or statement values are
  stored in the repository or test fixtures.
- `git diff --check` passed. No application dependency or lockfile changed.

Root backend command (run from repository root):

```text
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 \
  <isolated-python> -B -m pytest --noconftest --asyncio-mode=auto -q \
  backend/tests/test_operational_balance_statement_intake.py \
  backend/tests/test_operational_balance_statement_reconciliation.py \
  backend/tests/test_operational_balance_statement_fees.py \
  backend/tests/test_operational_balance_configured_provider_fees.py \
  backend/tests/test_operational_balance_provider_known_subtotals.py \
  backend/tests/test_operational_balance_financial_position.py \
  backend/tests/test_operational_balance_refund_capture_basis.py \
  backend/tests/test_operational_balance_payment_source_current.py \
  backend/tests/test_operational_balance_native_payment_compatibility.py \
  backend/tests/test_operational_balance_provider_settlement_guard.py::test_component_refund_after_settlement_is_blocked_without_actual_fee_credit_allocation
```

Frontend command:

```text
CI=true python3 scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watch=false --runInBand --resetMocks=false \
  --runTestsByPath src/pages/operational/ProviderStatements.test.jsx \
  src/pages/operational/providerStatementsApi.test.js \
  src/components/MezanV2NavigationShell.test.jsx \
  src/pages/operational/OperationalFinancialPosition.test.jsx
```

Local real-Mongo legacy integration was blocked because the dedicated local
Mongo service was absent; the attempted run was stopped (exit 130). Do not claim
those tests passed locally. The repository's existing operational CI includes
the real Mongo boundary; inspect results on the exact newly preserved head.

Visual browser verification is **blocked**, with no completed browser scenarios
or screenshots for the new page. Local Chromium failed before opening a page
with `socket() failed: Operation not permitted`. The cloud fallback could not
reach the loopback fixture and its subsequent error-page access was policy
blocked. Browser use was ended and the fixture server stopped. Do not retry via
alternate surfaces, expose a new endpoint, or bypass permissions for this gate.
The fixture build and component tests are not a substitute for visual acceptance.

Next safe action: preserve the final tree, inspect CI on that exact remote head
and record the outcome in the Draft PR. Retain the explicit visual limitation.
No merge, release intent, lease, production source refresh or deployment is
authorized by this checkpoint. Production changed: **no**.

The sections below preserve earlier milestones and are historical where they
describe in-progress work or superseded inclusive-average behavior.

## Scope and authority

- Task branch: `codex/provider-statement-reconciliation-20261115`.
- Verified production source base: `331b7917b3c01d064ee2a17affb4714f3a296331`.
- User approved implementation for Tabby, Tamara and Salla: upload directly inside
  Mezan 2 operational, detect the format, replace provisional fees with matched
  actual evidence, compare employee payment/refund records, and show discrepancies
  for explicit review and confirmation.
- Latest owner direction supersedes the interim rounded inclusive fallback:
  calculate a source-derived **variable commission percentage before VAT**.
  Exclude native fixed charges and payout fees from that percentage. Show the
  fixed component and explicit or configured VAT separately. Each provider and
  native currency keep their own source profile; unknown components remain
  incomplete. Known valid pricing and actual evidence have priority.
- Production changed: **no**. No release lease, live financial mutation, source
  refresh, bank posting, deployment or publication was performed for this task.
- Issue #1006 has reached GitHub's 2,500-comment limit. This task document and the
  task PR body are the continuation record; do not retry a known-full ledger.

## Change contract

The new operational intake is separate from the legacy settlement importer.
Original source values, native currencies, signed fees and tax, and source-row
locators are preserved. A row with missing or conflicting evidence remains
reviewable; missing amounts never become zero. A statement is not proof of a
bank receipt. Existing employee or provider events must never be posted twice.

The implementation is split across non-overlapping write surfaces:

1. Root: strict intake and synthetic workbook contracts, integration, continuity.
2. Reconciliation service: owner-scoped immutable statements, fresh matching,
   explicit confirmation, atomic state/receipt/audit and route guards.
3. Fee projection: accepted event bridge, actual/estimated distinction, safe
   Tabby fallback and report presentation.
4. Frontend: operational sidebar/page, upload, history, comparison and confirmation
   with request lifetime/session fences.

All processing is triggered by an explicit upload or action. GETs do not write,
refresh sources, or initiate background work. Existing cutover/opening balances,
recorded refund effects, foreign currencies, and source identities remain gates.

## Verified milestone: strict intake

Implemented `backend/operational_provider_statement_intake.py` and its synthetic
XLSX tests. The adapter reuses existing header/date conventions while retaining
strict financial evidence independently of legacy display defaults. It detects
one supported provider/table, retains decimal strings and native currency,
separates payout fees and Salla wallet purchases, and marks ambiguous repeated
rows. Tamara capture principal comes from the event amount, not the order total.

Verification (exit 0):

```text
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 \
  <isolated-python> -B -m pytest --noconftest -q \
  backend/tests/test_operational_balance_statement_intake.py --tb=short
21 passed
```

The new test initially failed collection because its implementation module did
not yet exist; this is evidence of an absent feature, not a reproduced old
accounting regression. Fresh execution after implementation passed all 21 cases.

The repository-pinned Node 22.23.2/Yarn 1.22.22 toolchain was verified. Frozen
frontend dependency installation completed with exit 0 and existing dependency
resolution/peer warnings. No lockfile or application dependency was changed.

## In progress / next safe action

- Complete and verify the connected service, fee projection and frontend slices.
- Run fresh combined tests and review the actual integrated diff.
- Exercise native XLSX upload through the authenticated operational API in an
  isolated synthetic database; test replay, overlap, stale approval, permission
  boundaries and existing refund aliases.
- Test actual fee substitution versus existing allocated fees and provisional
  estimates, including currency/partial/refund boundaries.
- Preserve a remote checkpoint and a reviewable PR before handoff.
- New-feature merge/deployment/publication is not part of the current checkpoint.

No customer records, uploaded merchant statements, credentials, environment
values, or real financial examples are included in this branch or its tests.

## Verified milestone: accepted fee projection

The first remotely preserved checkpoint is
`7ffde331d6c3b0f5417d91cd3adcf017b42e8645`, with Draft PR
https://github.com/AMASI-SA/AMASI-SA/pull/1938. Normal Git push did not have
credentials; the installed GitHub connector preserved the exact local tree,
which was verified equal before aligning the local task HEAD. No credential
probing or production change was used.

The next checkpoint adds the fee-only accepted-event bridge, explicit Tabby
average provenance and native-currency guard, retained evidence on refresh, and
actual/estimated provider-table breakdown. Actual fees replace the same covered
estimate. Duplicate, stale, partial-settlement and fixed-fee overlap stay under
review instead of being counted twice. The provider-specific evidence timestamp
does not change the saved source snapshot or profit period.

Root independently reran the combined intake/fee/presentation gate (exit 0):

```text
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 \
  <isolated-python> -B -m pytest --noconftest --asyncio-mode=auto -q \
  backend/tests/test_operational_balance_statement_intake.py \
  backend/tests/test_operational_balance_statement_fees.py \
  backend/tests/test_operational_balance_configured_provider_fees.py \
  backend/tests/test_operational_balance_provider_known_subtotals.py \
  backend/tests/test_operational_balance_financial_position.py --tb=short
147 passed
```

The intake now has 30 synthetic cases, including ambiguous duplicate financial
headers, retained missing order references, native installment metadata and the
normalized-evidence storage budget. The dedicated fee suite has 34 cases.

Frontend's first affected gate reports 148 tests passed across four suites. The
frontend owner is completing the final period-bound request contract and a
small desktop/mobile browser fixture. The reconciliation owner is implementing
the narrow explicit missing-refund confirmation and its authenticated API
contracts. Those ongoing slices are not claimed complete by this checkpoint.

Next: finish and independently verify the missing-event writer, including
replay, external duplicate proof, full-day cutoff containment, refreshing an
approved refund, and preserving the old direct-debit compensation behavior.
Then run the integrated backend/frontend checks and update the Draft PR. No
merge, release intent, lease, live refresh or deployment has been performed.

## Latest preserved checkpoint and active owner clarification

The accepted-fee checkpoint is remotely preserved at
`5db8012236a770581515293d8ca9fbca1a38a702` (tree
`ae4d89594dcd00a835d121635dbc1ffc34f6557e`). The first save was stopped; after
the owner said to continue, the GitHub connector preserved the exact verified
tree and the local branch was aligned without discarding ongoing edits.

The owner then clarified that the average percentage must exclude VAT, payout
fees and the fixed per-transaction charge. The source variable and fixed amounts
must be retained independently. An explicitly estimated fixed amount per sale
can be derived from the source as a separate component when the plan is missing;
it must never be presented as an actual fixed charge. Do not infer a variable
percentage from a format that does not separate its fixed fees. This work is
still being integrated, and supersedes the previous inclusive-average design.

Root verified the original privately supplied workbook read-only with the
strict intake. Its data stays outside Git. A plain single-cell note footer was
incorrectly classified as an event; a failing synthetic regression established
the defect, and the narrow correction now skips only nonfinancial note rows.
New source-component tests also exposed the legacy Tamara header map reading the
percentage column as fee money. The new operational reader now requires the
exact money header, without changing the legacy importer.

Fresh intake verification: **41 passed**, exit 0. The connected service owner
has a synthetic authenticated XLSX upload/confirm/replay test and is completing
native refund alias linkage, order-specific holds, metadata-only history and
compact receipts. The frontend's updated focused suite most recently passed
158 cases. Browser fixture build succeeded; local browser launch hit a runtime
socket restriction, and a supported alternate UI channel is being checked.
These are progress results, not final integrated acceptance or deployment.
