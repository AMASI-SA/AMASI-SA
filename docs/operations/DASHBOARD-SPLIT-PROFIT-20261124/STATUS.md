# Dashboard split-payment profit correction

## Scope and source integration

- Task: `DASHBOARD-SPLIT-PROFIT-20261124`.
- Repository: `AMASI-SA/AMASI-SA`.
- New release branch: `codex/dashboard-split-profit-release-20261125`.
- Preserved original task branch: `codex/dashboard-split-payment-profit-20261124`.
- Production target: `hotfix/prod-snap-meta-final`.
- Original base J: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.
- Current production/integration base J2:
  `f199a471f8bc6e05b475bb9b65a12107cbdc946b`, the normal merge of PR #2120.
- The user explicitly authorized upload, merge and publication on
  **25 November 2026, Asia/Riyadh**: "نعم ارفع وادمح وانشر".
  The earlier automatic approval rejection was resolved by that authorization.
  The later Git CLI push lacked a credential (`could not read Username`);
  authorized source checkpoints use the connected GitHub Git Data API.
- Original reviewed remote checkpoint:
  `6af60231b7a89e789c1f26600dcc8ad60b0589a0`, tree
  `210fe97c253e84dcd6798c6d9758cc2f4fe9289e`, exactly equivalent to preserved
  local checkpoint `764daef45a768f28bbc13cd103788aaf6316604a`.
  All 10 blob IDs, remote tree, commit and branch ref were independently checked.
- The subsequent source/status checkpoint is
  `9138afc446066d920d4373415f00e78b08520854`, tree
  `b210b92cb4d8f3caf5ae65f2fa1fa9f9a7cde0f8`.
  Its build work is historical following the production-base advance.
- Original implementation commit
  `05333b6682be0cf5125568503bdd0175b8212121` and its local successor remain
  preserved on `codex/dashboard-split-payment-profit-local-20261124`.
- [PR #2121](https://github.com/AMASI-SA/AMASI-SA/pull/2121) is the preserved
  historical candidate, superseded by
  [Draft PR #2122](https://github.com/AMASI-SA/AMASI-SA/pull/2122) on the new
  release branch. Do not merge the old candidate.
- Reviewed integrated source A2 is
  `6a55a5772318bc33af69622dc0c6e71bf85d7990`, tree
  `2e949590bd6bec8a5acc94734c8f78f976ea0d0e`, with sole parent J2.
- The enclosing source A3 has **one parent, A2**. It changes only the existing
  collector-context test expectation and this status document. All application
  bytes and the inherited intent remain unchanged. Read the remote release
  branch/PR head for the full enclosing SHA; this document does not embed its
  own commit SHA. A3 is source, not an intent-only Protocol-v5 B commit.
- Production changed by this task: **no**. No merge, shared `/app` change, release
  lease, publication, settings write, tariff change, sync, backfill or accounting
  post has occurred.

## Confirmed defect

The live executive dashboard withheld net profit with
`provider_split_payment_review_required`, while product-cost, currency and
financial-cohort completeness were true. The generic UI message made this look
like a missing commission configuration. Two native, fully paid SAR payment
shapes were unsupported: a card plus the internal customer wallet, and two mada
transactions with distinct references. Customer details and real transaction
references are not retained here or in fixtures.

## Implemented behavior

- `backend/dashboard_split_payment_fees.py` reuses the existing split-evidence
  validator for owner-bound, current, fully paid Salla transactions. It applies
  the saved capture tariff to each distinct external transaction only when that
  tariff is valid throughout the possible payment interval. No capture date is
  invented. Repeated identical references do not incur another fixed charge.
- `backend/dashboard_payment_fees.py` retains bounded native payment evidence,
  binds the owner context, detects selected-cohort reference collisions and
  exposes independently verified internal-wallet principal. Actual fees keep
  priority over tariff estimates.
- `backend/legacy_dashboard_reader.py` retains wallet redemption in sales and
  profit, while excluding it once from electronic principal and expected cash
  transfer. Unknown wallet evidence leaves cash-like totals unknown even when
  imported actual fees remain valid. Status-filtered electronic cohorts retain
  separate before/after completeness.
- The frontend completeness helper explains pending split-payment matching and
  continues hiding unknown or stale profit values.
- The existing payment-fee CI workflow includes the new module and regression
  files. No operational posting code or legacy fixed-fee contract changed.

Unsupported, ambiguous, foreign-currency, refunded, cancelled, stale or
cross-order-conflicting splits remain guarded. Duplicate-reference detection is
bounded to the already selected owner cohort and yields through the CPU budget.

## Synthetic acceptance values

These are configured-tariff estimates, not actual settlement fees or a current
live profit claim.

| Synthetic tender | Fee net | Fee VAT | Fee total |
| --- | ---: | ---: | ---: |
| Card 145.86 + internal wallet 33.01 | 4.21 | 0.63 | 4.84 |
| Mada 329.40 + distinct mada 198.72 | 7.28 | 1.09 | 8.37 |
| Combined | 11.49 | 1.72 | 13.21 |

For the canonical dashboard fixture with full sales 706.99, product cost 20,
and no advertising/shipping costs, profit is 673.78, internal wallet 33.01,
electronic principal 673.98, and expected transfer/electronic net 660.77.

## Historical implementation verification

Original positive cases first reproduced the split-payment block. Cash-basis
tests also failed before integration: expected transfer incorrectly included
wallet principal. The frontend regressions first reproduced the generic hint.

The reviewed implementation passed **489 tests** in total. These completed
local checks remain historical evidence for the preserved implementation; final
source-head CI and the new governed build are still required:

- **300 backend tests**, exit 0, including all current payment-fee workflow
  suites, four canonical read-only cash-basis cases, 45 split-fee cases and the
  seven unchanged legacy same-rail fee cases.
- **189 frontend tests in seven suites**, exit 0, including every current
  payment-fee workflow suite and executive-profit arithmetic. Repeated under
  the pinned Node 22.23.2/Yarn 1.22.22 release toolchain: 189 passed, exit 0.
- Python syntax checks and `git diff --check`, exit 0.
- Independent code review: no remaining blockers. Concrete refund/cancellation,
  projected currency, and wallet-cash findings were fixed and regression-tested.

The first combined backend command failed collection because a legacy test
imports sibling modules without the package prefix. Re-running with
`PYTHONPATH=backend:backend/tests` passed; no application change was needed.
Existing Pydantic/React test deprecation warnings remain unrelated.

Backend reproduction from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_dashboard_v2_ad_account_costs.py \
  backend/tests/test_dashboard_weekly_payment_fees.py \
  backend/tests/test_dashboard_operational_statement_fees.py \
  backend/tests/test_provider_fee_statement_actual_sources.py \
  backend/tests/test_provider_fee_weekly_average.py \
  backend/tests/test_provider_fee_weekly_sources.py \
  backend/tests/test_dashboard_dated_payment_fees.py \
  backend/tests/test_dashboard_split_payment_fees.py \
  backend/tests/test_dashboard_split_payment_cash_basis.py \
  backend/tests/test_dashboard_card_snapshots.py \
  backend/tests/test_dashboard_weekly_read_model.py \
  backend/tests/test_dashboard_demand_builders.py \
  backend/tests/test_operational_balance_configured_provider_fees.py
```

Frontend reproduction from `frontend` with installed project dependencies:

```bash
CI=true node node_modules/react-scripts/scripts/test.js \
  --watch=false --runInBand --resolver ./scripts/jest-package-exports.cjs \
  --runTestsByPath \
  src/components/ProfitSummaryCard.paymentFees.test.jsx \
  src/pages/AdvancedDashboard.paymentFees.test.jsx \
  src/components/DashboardAnalyticsPlacement.test.js \
  src/pages/AdvancedDashboard.cardRefresh.test.jsx \
  src/hooks/useDashboardCardSnapshot.test.jsx \
  src/lib/demandRead.test.js src/lib/dashboardExecutiveProfit.test.js
```

## Historical build evidence

The governed Node 22.23.2/Yarn 1.22.22 frozen-lockfile installation, two-pass
frontend build and `verify_frontend_build.py` passed for original implementation
commit `05333b6682be0cf5125568503bdd0175b8212121` (exit 0). The local build used
task-owned dependencies preseeded from an existing cache; it was not the
clean-clone adapter rehearsal. The borrowed symlink was removed before the copy,
and other tasks' caches/checkouts were not changed.

- Frontend artifact tree:
  `59de118c0e3fc1da1f837d09335725fb24ccd2898de28551f71271d9e756740b`.
- Reproducibility proof:
  `c1c14b1074e0b44efd0ab0b50bbe87eab0b5f396675de794f709db405f084e1f`.
- Both passes retained the same build-meta SHA256:
  `d6b7e94b78a839c539c170020b7217820e0c166921d2e66cf9ceab9e1d0460b2`.

Additional build work was bound to source
`9138afc446066d920d4373415f00e78b08520854` while the production branch advanced.
That evidence is historical too. A2 subsequently completed its governed two-pass
build, verification and external freeze, but its later CI test-expectation
failure requires a fresh A3 build and intent. The superseded A2 candidate had:

- Source: `6a55a5772318bc33af69622dc0c6e71bf85d7990`.
- Intent bytes: 1,531,723; SHA256
  `b5e5a87de13bad59a1640a4ed0202a611ccb21a2471b73a7f93c312a3bc427e1`.
- Runtime ID:
  `rg5-529dbcf25fd156d81c277a70335a3c097bbf648d4aebfefce054436fa873d387`.
- Artifact tree:
  `3c51000b2a0c0f86bb01a5d29726170c47137db286a82d4a49a7cc19580f7690`.
- Reproducibility proof:
  `21682042b2f8bead811ea06fae54a3ca09ca8f55cde08c95e1d1afd3b3769773`.

The original `load_candidate_release_intent` independently accepted that external
A2 file with exact source/base, public origin and deterministic identity, exit 0.
The B uploader stopped before any remote blob, tree, commit or ref mutation when
CI reported the source-test blocker. No B was created for A2. Do not reuse this
historical intent for A3; rebuild and freeze against the new exact source SHA.

## CI follow-up and source A3

[A2 four-platform backend CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45492074123/job/138792731397)
reported 448 passed, one failed and seven subtests passed. The failure was
`test_existing_hydration_query_carries_only_precise_source_time_and_settings_without_extra_reads`
in `backend/tests/test_dashboard_ads_account_results.py`, at its exact-context
comparison. The existing expectation omitted the deliberately added owner-bound
context fields `owner_id="a"` and `split_fee_reference_conflicts=[]`.

The unchanged A2 test reproduced that precise assertion locally: one failed,
exit 1, in 3.47 seconds. A3 adds only those two expected values on one line.
The dictionary comparison remains exact; the no-extra-query counts, source
context nonmutation, bounded hydration, private-field exclusions and endpoint
result assertions remain intact. No application change or assertion relaxation
was needed.

Verification after that correction:

- Isolated regression: one passed, exit 0, in 3.28 seconds.
- All 19 test files selected by the `backend-contract` job in
  `.github/workflows/prod-dashboard-four-platform-spend-v1.yml`:
  **449 passed and seven subtests passed**, exit 0, in 17.33 seconds.
  Five existing Pydantic deprecation warnings remain unrelated.
- The earlier exact-A2 payment-fee regression also passed all 300 tests,
  exit 0, in 16.45 seconds. These overlapping suites are not additive unique
  test counts.
- `git diff --check` passed. The source correction is one added test line;
  this status document is the only other changed path after A2.

The first full local attempt encountered environment prerequisites: the cached
review environment lacked SOCKS support, and the clone lacked nine historical
blob objects required by the existing source-parity oracles. `socksio==1.0.0`
was installed in a task-owned target directory, with the shared review environment
and configured network proxy unchanged. The nine original blobs were read from
GitHub, verified against their pinned Git IDs, and added only to the object
store. No oracle, Git ref or application file was changed. The successful run
used those prerequisites and disabled repository bytecode/pytest-cache writes.

Portable reproduction from `backend`, with the workflow dependencies installed
and its pinned historical source objects available:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=.:tests python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short \
  tests/test_dashboard_four_platform_spend_v1.py \
  tests/test_dashboard_tiktok_native_spend_basis.py \
  tests/test_dashboard_snapchat_spend_issue_727.py \
  tests/test_google_ads_reporting_v1.py \
  tests/test_dashboard_ads_hourly_spend_v1.py \
  tests/test_dashboard_ads_account_results.py \
  tests/test_dashboard_ads_costs.py \
  tests/test_dashboard_dated_payment_fees.py \
  tests/test_dashboard_ads_periods.py \
  tests/test_dashboard_runtime_source_contract.py \
  tests/test_dashboard_card_snapshots.py \
  tests/test_payment_method_setup.py \
  tests/test_payment_method_fee_status.py \
  tests/test_awaiting_payment_cohort.py \
  tests/test_payment_method_setup_routes.py \
  tests/test_dashboard_bundle_cost_authority.py \
  tests/test_snapchat_reporting_v2_account_display.py \
  tests/test_tiktok_scheduler_hourly.py \
  tests/test_ads_auto_sync_scheduler_v2.py
```

## Why a fresh linear source is required

PR #2120 merged at 2026-11-24T21:23:10Z as
`f199a471f8bc6e05b475bb9b65a12107cbdc946b`, advancing production while the
dashboard source was being built. Its 28 changed paths have no overlap with
the ten dashboard paths from J to `9138afc...`.

A trial merge with first parent J2 and second parent `9138afc...` failed the
original `_assert_candidate_source_transition` check: the complete candidate
history included an intent change. That trial is not a releasable source and
was not published. The validator remains unchanged.

The replacement source A2 has J2 as its sole parent. It overlays the exact nine
reviewed application/test/workflow blobs from `9138afc...`, plus this updated
status document, onto J2. It does not include the old source as a second parent
or rewrite either preserved branch. Every PR #2120 path and its inherited
`release/release-intent-v5.json` blob
`ba1de70733b798225dd8aa39a4c7569b55df3a4c` are preserved.

The only A2 changed paths relative to J2 are:

- `.github/workflows/prod-dashboard-v2-ad-fx-payment-fees.yml`
- `backend/dashboard_payment_fees.py`
- `backend/dashboard_split_payment_fees.py`
- `backend/legacy_dashboard_reader.py`
- `backend/tests/test_dashboard_split_payment_cash_basis.py`
- `backend/tests/test_dashboard_split_payment_fees.py`
- `docs/operations/DASHBOARD-SPLIT-PROFIT-20261124/STATUS.md`
- `frontend/src/components/ProfitSummaryCard.paymentFees.test.jsx`
- `frontend/src/lib/paymentFeeCompleteness.js`
- `frontend/src/pages/AdvancedDashboard.paymentFees.test.jsx`

The A2 uploader independently calculated the desired Git tree from J2 and these
exact blobs, required the remote tree to match, and confirmed production still
at J2 before creating the absent release branch. A3 now extends A2 linearly with
the one-line test correction and this updated document. Its only additional
path relative to J2 is `backend/tests/test_dashboard_ads_account_results.py`.
The full J2-to-A3 history still carries no intent change. Both source uploads
preserve the earlier branches and every PR #2120 application/intent byte.

## Published-base evidence and shared release coordination

The initial dashboard diagnosis observed live frontend source
`a611477be8d52b18f9ad851f44facf9c78be2905`. Later
[PR #2118 publication evidence](https://github.com/AMASI-SA/AMASI-SA/blob/65b65d107b59ea9ba83e4a891bf24163dacef9f3/docs/operations/OPERATIONAL-COST-SOURCE-DIAGNOSTICS-20261124/STATUS.json)
reports a newer platform publication:

- Source: `6e25a762b17ad87ce8efeb37f2952a478ae9427b`.
- Intent-only B: `a0f5bd77f91b85b2d75885aa4173c4e48aa16ad7`.
- Reported PR #2118 merge/deployment J (now a production ancestor):
  `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.
- Runtime ID:
  `rg5-b7cabd1d3fb44fd96dee501e461a869b40872100213fd64c55665539fe9ab97e`.
- Platform version `eeecd3f`, reported finished 24 November at 23:41 Riyadh.

That immutable PR #2118 record still described its original three-probe verifier
as running. A later PR #2120 continuation observation at 21:01 UTC reported an
inactive shared guard. These are other tasks' recorded observations; this task
has not yet independently confirmed the current shared guard or final live
identity. The actual original `/app` guard status must be read before any shared
workspace mutation or release rehearsal. Preserve every other owner's lease.

[PR #2120](https://github.com/AMASI-SA/AMASI-SA/pull/2120) is now merged into
production as J2. Its warehouse changes and newer tracked intent are retained in
the new dashboard source. The current published identity and that release's
completion still require independent confirmation; a GitHub merge alone does
not establish a successful publication or an inactive shared lease.

Root must confirm the current publisher's state before this task proceeds with
shared-host work. If the production or published base changes again, rebuild
from the newly reviewed exact source/base and freeze a fresh intent. Preserve
other owners' leases and keep the original release validators unchanged.

## Release state and exact next actions

Upload, merge and publication remain authorized. Original PR #2121 and its
source history are preserved; Draft PR #2122 is the release candidate. Corrected
source A3 still requires its exact-head CI/review, governed build, new intent-only
B, original adapter rehearsal and publication verification.

1. Read and verify remote A3: sole parent A2, exactly the test and status paths
   changed after A2, all application bytes unchanged, and no inherited intent
   change anywhere in J2-to-A3 history. Preserve the verified local edits before
   adopting that exact remote source; retain both earlier branches.
2. Re-read Draft PR #2122 and exact-head CI after the source update.
   Confirm current production J2, current published identity, and the actual
   original shared guard before any shared workspace action. Wait for the
   active publisher if its lease remains active; do not clear or reuse it.
3. Follow `AGENTS.md` and `docs/PRODUCTION_RELEASE_PROTOCOL.md`: run the original
   candidate-transition check, build exact A3 with the governed toolchain,
   validate its candidate, and freeze B changing only
   `release/release-intent-v5.json`. Require exact-head CI/review and a normal
   merge preserving A3/B ancestry.
4. Complete the original adapter rehearsal and Release Guard preparation and
   prepublication checks. Publish once through the authorized production flow.
5. Require a newer explicit deployment success and all three original verifier
   probes with the intended source/runtime identity, then inactive guard status.
   Check current executive-dashboard payment completeness and profit afterward.

Root owns PR/continuation-ledger updates, source adoption, intent freeze, merge
and publication. This integration preserves prior branches and other tasks'
source/intent. No post-release dashboard or live-profit claim is made.

## Operator evidence checkpoint — 25 November 2026, Asia/Riyadh

This append-only checkpoint is on the separate branch
`codex/dashboard-split-profit-evidence-20261125`, based directly on verified
remote source A3 `10bb33957c8c2994814a76ba5feed1182336ebda`, tree
`0941ce9c29e3515346e2f31eac161e9986730e68`. A3 has sole parent A2
`6a55a5772318bc33af69622dc0c6e71bf85d7990`. The evidence branch leaves the
release branch and frozen-source history untouched; do not merge it into the
Protocol-v5 candidate. Its enclosing commit is an evidence checkpoint, not B.

The A3 test correction is backed by the recorded complete four-platform backend
selection: **449 tests and seven subtests passed**, exit 0, in 17.33 seconds.
Those are local verification results, not a claim that all current CI or the
new governed build has completed. [PR #2122](https://github.com/AMASI-SA/AMASI-SA/pull/2122)
remains the release candidate; PR #2121 remains superseded.

The checkpoint adds the operator under `operators/pr2122_guarded_rehearsal.py`
and its README, complete adaptation diff, adapted isolated checks, result log
and verification manifest under `verification/operator/`. The operator SHA256 is
`98719b39a298c98c5a56d517bcdf2edd3f350497e53bd05aadf4202a2d43c65b`.
It retains the original PR #2092 preservation/lock/timeout/argument behavior,
changes J to `f199a471f8bc6e05b475bb9b65a12107cbdc946b`, updates only task labels,
and strips inherited bootstrap intent from the official adapter environment.

All five isolated operator check groups passed, exit 0, in 1.053 seconds, with
the helper main and actual shared host never invoked. Independent operator
review passed with the explicit requirement to invoke it using
`PYTHONOPTIMIZE=0 PYTHONDONTWRITEBYTECODE=1 python -B`; the reviewed helper bytes
were not changed after review. The artifacts contain no secrets, credentials,
customer identifiers or private environment values.

The exact A3 governed build, fresh intent-only B, exact-D CI/review, merge,
shared-host rehearsal and publication verification remain pending at this
checkpoint. No envelope was created. It will be created only after D and the
fresh actual untracked-entry count are known, using the exact reviewed A/B/D,
common B/D tree and deterministic runtime identity. The prior publisher's
running original guard operation must finish before another guard call or
shared-host action; never use or clear another owner's lease.

Next safe action: the root owner completes the current A3 build and original
candidate validation, freezes a new intent-only B, and checks exact-head CI
and the preceding publisher's completion. The operator README supplies the
remaining prerequisites and mandatory invocation. Root owns the PR and Issue
#1006 continuation-ledger updates. Production changed by this evidence work:
**no**. No source-checkout edit, release-branch update, PR metadata change,
merge, host action, lease, publication, sync or accounting write is included.

## Build and intent checkpoint E2 — 25 November 2026, Asia/Riyadh

This checkpoint extends evidence commit
`e71a46281a4d61c5235abf1a8ba26f8c5dad3a97` on the same separate evidence branch;
it does not rewrite E1 or change the release source/intent branch. The pending
build/B statements in the preceding E1 checkpoint and operator README describe
that earlier milestone and are superseded by the results below.

The governed A3 two-pass build, source verification, external intent freeze,
original candidate validation and B reviewed-intent frontend verification
completed successfully, exit 0. The unchanged pinned toolchain was Node
22.23.2 and Yarn 1.22.22. The compact record is
`verification/release-validation.json`; full verifier manifests are not copied
into this evidence checkpoint.

- Source A3: `10bb33957c8c2994814a76ba5feed1182336ebda`.
- Source base J: `f199a471f8bc6e05b475bb9b65a12107cbdc946b`.
- Intent-only B: `c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7`.
- B tree: `fa0f36e01d3c3a706a0cbdb23aa1cfcd0b8f09a1`.
- Runtime ID:
  `rg5-71a053d9aff1c8c4dec4f5b5e9171630b8c53bf6fea6d3516427bd7836c4ba9c`.
- Frozen intent SHA256:
  `0140731cf2c829fcae31c4901e4385ce043982473ca682337c1fbc2a825365c5`.
- Frontend artifact tree SHA256:
  `3c51000b2a0c0f86bb01a5d29726170c47137db286a82d4a49a7cc19580f7690`.
- Reproducibility proof SHA256:
  `ce4f71126eb00c1df3af1c40c8ab00ec10e5b0deaa770be4e33703386e0fb1b7`.

Independent evidence checks confirmed remote B is exactly one intent-only
commit after A3. Its immutable local Git object has the stated SHA/tree and
sole parent A3. The frozen intent bytes match the stated SHA256 and source/base
identities. Source and B verification JSON bytes are identical; their full
frontend/proof contracts match the tracked B intent. Both clean passes have
the same artifact tree and build-meta SHA256, with pass 2 retained.

The independent final A3/B packet review also passed, exit 0, without writes.
It verified all 3,138 governed source records (1,450 frontend, 1,359 backend and
329 release controls), six critical hashes, 383 artifact files excluding
build-meta (381 public files), exact build-meta bytes, both identical proof
passes and the rederived runtime ID. It also confirmed the remote ancestry,
intent-only B, frozen bytes/hash and empty intent history throughout J-to-A3.

The coordinator reports PR #2122 ready for review, with final CI for exact B
still running. Merge D does not yet exist for this task, so no execution
envelope is created. The preceding PR #2120 publisher's E5 operator stopped on
`FOREIGN_ENTRY_CHANGED`; that owner is resolving its own preserved state. This
task has not touched the shared host or borrowed/cleared another owner's lock
or lease.

Next safe action: the root owner waits for all required exact-B CI and the
preceding publisher's verified completion, then independently rechecks the
production base before a normal merge. The reviewed operator remains unchanged
and requires `PYTHONOPTIMIZE=0` during any eventual invocation. Production
changed by this evidence work: **no**. No source edit, PR metadata change,
release-branch mutation, merge, host action, lease or publication was performed.

## Continuity checkpoint E3 — historical operator must not run

This checkpoint extends E2
`5985afecf247b515ed2ae673e6049c543d54dbe2` on the separate evidence branch.
**The E1 `pr2122_guarded_rehearsal.py` is historical and MUST NOT RUN.** Its
helper, tests, recorded results and hashes are preserved unchanged. The
earlier operator invocation is withdrawn pending a reviewed replacement.

The predecessor's immutable
[E6 evidence](https://github.com/AMASI-SA/AMASI-SA/commit/2e4bc4140b77472127e765be4ec6811aed1a6e0d)
documents the concrete reason: the original foreign-file equality check
encounters an already running, separate Mongo service under
`.worktrees/p01-acceptance-state-20260919/mongo` and its sibling `mongo.log`.
The predecessor v1 owned-clone adapter passed, then its preservation check
stopped with `FOREIGN_ENTRY_CHANGED` before generated backups, source
fast-forward or the canonical `/app` adapter. Its full snapshot covered
193,825 records; it cannot prove stable live database bytes. This is not an
approval-system rejection, and it does not justify stopping the service or
weakening the original release controls.

That owner is preparing a separate independently reviewed v2 helper with a
narrow, explicit live-service boundary. Wait for its immutable helper, envelope
schema, tests and review before adapting it for this task. Preserve the exact
process/start, executable, command-line and real directory identities; only
that existing Mongo service's data descendants may change. Keep the sibling
log append-only under its identified existing writer with unchanged file
identity and original prefix. Every other foreign record remains exact, and
all source, adapter, staging and backup write paths must be disjoint. Do not
implement a parallel workaround or run the old helper.

The frozen dashboard source and intent identities remain unchanged:

- A3: `10bb33957c8c2994814a76ba5feed1182336ebda`.
- Source base J: `f199a471f8bc6e05b475bb9b65a12107cbdc946b`.
- B: `c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7`.
- B tree: `fa0f36e01d3c3a706a0cbdb23aa1cfcd0b8f09a1`.
- Runtime ID:
  `rg5-71a053d9aff1c8c4dec4f5b5e9171630b8c53bf6fea6d3516427bd7836c4ba9c`.

At the coordinator's latest exact-B CI observation, two tests remained pending
and no failures were reported. This is not an all-green result. PR #2122 has
not been merged by this task; no D or execution envelope has been created.
This task has made no shared `/app` change, lease or publication.

Next safe sequence: obtain the immutable v2 packet and independently review
the minimal task/base adaptation; wait for the preceding publisher's successful
publication, original verification and lease closure; require final exact-B
CI, confirm the production base, then perform the normal reviewed merge D and
exact-D CI. Only afterward may a fresh, reviewed v2 execution envelope bind
the final identities and current host preservation observations. Any eventual
invocation must retain `PYTHONOPTIMIZE=0` and the unchanged original guard and
adapter gates. Production changed by this evidence checkpoint: **no**.


## Exact-B CI and packet review checkpoint E4 — 25 November 2026, Asia/Riyadh

This checkpoint extends E3
`da68f358e26488917138a98a3300afa47e72b663` on the separate evidence branch.
All preceding STATUS bytes and evidence artifacts remain unchanged. The
recorded E3 pending-CI observation is superseded by the results below.

The independent immutable review record is
`verification/pr2122-B-final-review.json` (168,719 bytes; SHA256
`468a89e28f9bbca2f364ba711c21a91a97b8a66db5490deeea3c9377ae6970e3`).
It observed the exact-B checks at 2026-11-24 21:59:48 UTC and the final
refs at 22:00:20 UTC. All 46 checks were completed: 36 succeeded and 10 were
reviewed conditional skips, with zero queued, running, failed, neutral or
cancelled checks and no legacy commit statuses. The skips follow the
documented workflow event, branch or unchanged-scope conditions; both scope
guard runs succeeded. The coordinator's independent 22:03 recheck also
reported all checks green.

At those observations, PR #2122 was open, ready for review, mergeable and
clean, with exact head B and base J:

- A3: `10bb33957c8c2994814a76ba5feed1182336ebda`.
- J: `f199a471f8bc6e05b475bb9b65a12107cbdc946b`.
- B: `c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7`.
- B tree: `fa0f36e01d3c3a706a0cbdb23aa1cfcd0b8f09a1`.

The tested synthetic merge
`93225ab99ba7e21c4a0c75cf95114c7d80573953` has ordered parents [J, B]
and exactly the frozen B tree. It is CI review evidence; this checkpoint
does not claim an actual production merge D.

Independent packet verification passed for all 3,138 governed source
records, all 383 artifact files excluding build-meta, all six critical
hashes, the exact build metadata, both reproducibility proof passes and the
independently derived runtime identity:

`rg5-71a053d9aff1c8c4dec4f5b5e9171630b8c53bf6fea6d3516427bd7836c4ba9c`.

The review also confirmed source/base tree bindings, recorded source deltas,
empty intent history throughout J-to-A3, exact frozen intent bytes and the
intent-only B transition. It made no source or host writes and reran no tests.

**The historical E1 `pr2122_guarded_rehearsal.py` MUST NOT RUN.**
The frontend owner is adapting the predecessor's immutable, independently
reviewed E7 live-service-scope v2 packet. This checkpoint does not approve or
execute that adaptation. Preserve E1, E2 and E3 as history and wait for the
separate adapted v2 helper, envelope schema, tests and independent review.

The preceding publisher must complete its successful publication, original
verification and lease closure **before this task merges**, as well as before
any shared-host guard, rehearsal or publication. The root owner must then
recheck the production base and exact-B checks before the normal reviewed
merge, verify exact-D CI and bind a fresh reviewed v2 envelope to the actual
D, unchanged B/D tree, runtime identity and current host observations. Retain
`PYTHONOPTIMIZE=0` and every original guard and adapter gate.

Production changed by this evidence checkpoint: **no**. No main-checkout or
source edit, release-branch update, PR mutation, merge, host action, lease,
publication, sync or accounting write is included.

## Reviewed live-scope operator checkpoint E5 — 25 November 2026, Asia/Riyadh

This checkpoint extends E4
`f07abdaf0c2639514319ea05098a35c99b0b4574` on the separate evidence branch.
All earlier STATUS bytes, E1 helper/tests and E2–E4 artifacts remain unchanged.
The historical E1 `pr2122_guarded_rehearsal.py` still **MUST NOT RUN**.

The new `operators/pr2122_release_operator_live_scope.py` is minimally adapted
from the predecessor's immutable, independently reviewed E7 helper at
`a6853cf13af7a64fe2443eea588de12f43744066`. The exact upstream bytes and hashes
were checked. Only nine literals changed: five reviewed A/J/B/tree/runtime
identities and four task-description/receipt/stage/import labels. The normalized
AST and complete live-scope source remain identical. No guard, lock, capacity,
owned-clone, backup/fsync, timeout, lineage, adapter, package/proof or preservation
logic changed.

The adapted helper is 43,582 bytes, SHA256
`62e545a2b11520f121aebf0a985b0854134052070ff7355892fcec0f95eb72c7`.
The complete diff and review packet are retained under
`verification/operator-live-scope/`. Independent adaptation review passed with
no additional code blockers; the root also reviewed the diff and main flow.
AST/static restriction checks and all **six isolated fixture groups passed**,
exit 0, in 0.366 seconds. Those checks cover the exact unaccessed DB subtree,
strict sibling preservation, append-only log prefix and identity, lexical and
resolved write collisions, synthetic process/root identity mismatches, all four
capacity-checked generated backups, and the owned adapter environment. They did
not invoke main, the protected rehearsal, Git/network, the adapter, actual
`/app`, services or a database. This is not a completed host rehearsal.

The preserved v2 implementation pins the existing Mongo process/start,
executable/command-line and real parent/root identities. It permits only that
specific data subtree's own descendants to change without traversing or reading
them. The independent sibling log retains its exact file identity and complete
original prefix. The E6-observed supervisord PID/start/fd is **not revalidated
by this implementation**; no supervisor-writer binding is claimed. All other
foreign records remain exact and every incoming/operator/adapter write target
must remain disjoint.

**Invocation correction:** the unchanged v2 gate rejects any presence of
`PYTHONOPTIMIZE`, including `0`. This supersedes the earlier E1/E3/E4 invocation
advice. Any eventual reviewed invocation must use
`env -u PYTHONOPTIMIZE PYTHONDONTWRITEBYTECODE=1 python -B`, with the exact helper
and a separately reviewed final envelope. The ten-field schema is documented
in the new README; no dashboard D or execution envelope exists yet.

Source A3 `10bb33957c8c2994814a76ba5feed1182336ebda`, base J
`f199a471f8bc6e05b475bb9b65a12107cbdc946b`, and intent-only B
`c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7` remain frozen. B tree is
`fa0f36e01d3c3a706a0cbdb23aa1cfcd0b8f09a1`, with runtime ID
`rg5-71a053d9aff1c8c4dec4f5b5e9171630b8c53bf6fea6d3516427bd7836c4ba9c`.
The E4 final exact-B result remains 36 successful checks and ten reviewed
conditional skips, with no failures or pending checks at that observation.

The preceding publisher's immutable E8 checkpoint
`ed0ee0db9ef120c58ba9a5f763c6dbaf55855f3b` records that its v2 run started in its
own stage. That is not successful publication or lease-closure proof. Wait for
that owner's complete rehearsal, successful publication, original verification
and closure; do not run a duplicate helper or call its lock-taking guard while
its operation is running. Root must then recheck the production base and final
exact-B CI before merging this task, verify exact-D CI and prepare a fresh
reviewed envelope with matching current preservation identities and count.

Production changed by this evidence checkpoint: **no**. No source-checkout
edit, release-branch update, PR mutation, merge, shared-host action, lease,
publication, service change or database read/write was performed. Evidence
persistence does not authorize bypassing any remaining deployment prerequisite.

## Predecessor publication observation E6 — 25 November 2026, Asia/Riyadh

This evidence checkpoint extends E5 `0beba15679fed2ada56896a4847b0368040e8edd` and preserves the frozen source/intent and all earlier artifacts. The compact current record is `verification/predecessor-publication-wait.json`.

The root's management UI now shows new published version `7191418`, with Deployment finished displayed at 25 November 01:42 Riyadh. A fresh live `/build-meta.json` page shows predecessor source `b236f51bb6c52e6f5d14a136db92f7474e4f5873`. These establish a newer platform/front-end publication observation, not completion of the predecessor's original three-probe verification.

The predecessor evidence branch still ends at E11 `3ca93d49b1c0071824a374bb7b1d1ee33a814a9f`. A read-only observation of its exact lease file shows its own actor and 22:29:50 UTC preparation still present. The root did not invoke its guard, close or reuse its lease, modify its files, or perform its business action. No current original verification result or closure has been recorded yet.

Fresh exact-B CI remains 36 successful checks and ten reviewed conditional skips, with no pending or failed checks. PR #2122 remains ready, mergeable and clean at B `c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7`; production remains J `f199a471f8bc6e05b475bb9b65a12107cbdc946b`. D and our execution envelope do not yet exist. The reviewed E5 helper remains ready, unchanged, and unexecuted; historical E1 must not run.

Next safe action remains predecessor original verification and closed lease, then fresh original guard/host/base checks, the already authorized normal merge, exact-D CI and independently reviewed final envelope. Production changed by this task: **no**. No own shared-host source change, lease or publication, and no financial/settings/sync/backfill write.

## Host identity checkpoint E7 — independently reviewed v3

This checkpoint follows evidence E6
`3174ca0d7ecc087dbf306213ca19b4f3f55b9ee5`. It preserves all frozen application
source, intent, earlier evidence and historical operator bytes. No deployment
commit D or dashboard execution envelope exists yet.

After the code-server reconnect, root observed the old pinned Mongo PID 96507
and prior `/tmp/mezan-cabinet-archive-release-live-e7l6f9wi` stage were absent.
The checkout remained clean at J
`f199a471f8bc6e05b475bb9b65a12107cbdc946b`, and the exact predecessor lease
remained present. These are observations of changed state; no specific cause or
full-host replacement is claimed. E5's old pins must not execute on that state.

Root's two fresh process/root observations identified the same exact dbpath,
full command hash and executable path, now at PID 311 with stable start ticks
40102116. `/proc/311/exe` and the direct regular executable matched identity
`[4194406,85818,493,0,0]`. The protected directory and log device is now 66340;
their prior inodes, modes and uid/gid remain unchanged. All parent/root paths
were real nonsymlink directories and the log was regular. No DB descendant or
log content was read in those observations. Fresh nonignored entry count is
432; subsequent operator checks must measure the current state again.

The v3 helper proposal
`operators/pr2122_release_operator_live_scope_v3.py` is 43,579 bytes, SHA256
`d7832f19f5475912f3ebd0ba14c4fd2cd9b61572426f67190719c507d655a343`.
Exactly eight integer literals inside `LIVE_SCOPE` changed from E5: PID, start
ticks, executable device/inode, three directory devices and the log device.
Reverse substitution restores E5 exactly, and the entire AST outside that
assignment is unchanged. No release identity, scope path, command hash, guard,
adapter, capacity, backup, preservation, timeout or environment rule changed.

All six existing isolated fixture groups passed, exit 0, in 0.25 seconds, with
empty stderr. The fixture harness differs only in its expected helper hash.
Main, host rehearsal, subprocess runner and adapter were forbidden during these
tests. Root independently reviewed the exact eight-literal diff, normalized AST
and final bytes/hash and reported PASS. The complete proposal, diff, fixture
results and metadata are retained under `verification/operator-live-scope-v3/`.
These results do not establish a successful actual-host rehearsal.

Root's later original public guard status returned exit 0, `active=true`, with
the same predecessor actor/source/deployment/release and no incomplete lease
directory. Original verification and closure by that owner remain required.
This task has not called its verifier, cleared or reused its lease, or changed
shared source. A missing previous stage is not authority to recover or delete
another owner's state.

The newly reviewed baseline must still retain the exact Mongo process/root/log
identities throughout the eventual run. DB descendants remain unaccessed; the
log retains the initial prefix and identity captured for this new baseline.
No uninterrupted historical byte continuity across the reconnect is claimed.
The ten-field envelope schema and `env -u PYTHONOPTIMIZE
PYTHONDONTWRITEBYTECODE=1 python -B` invocation remain unchanged.

Next safe action: wait for the predecessor owner to complete original verification
and lease closure, freshly confirm guard inactivity, production J and final exact-B CI,
perform the authorized normal merge and exact-D CI, then review the actual
D-bound envelope and current host pins before executing the exact reviewed
helper once. Production changed by this task: **no**. No host helper execution,
dashboard lease, publish, service operation, database query, financial write,
source change or automatic rollback was performed by this review.

A second independent reviewer also approved these exact frozen bytes for durable checkpoint and later gated use. It independently rehashed E5/v3, verified the eight AST integer changes, exact reverse-restored bytes, unchanged policy/control flow, and recorded fixture-file hashes and six-group result. It did not rerun tests or access the host. Original E11 evidence and the active predecessor lease still block the next release; this approval is not execution or lease-takeover authority.


## Evidence checkpoint E8 — restore complete STATUS history

- The STATUS file in E7 (`b4cfb9d362011e849c84f9734f1b4aacf5199b0b`) accidentally began with the literal `undefined` and omitted its earlier status text. This evidence-only repair restores the complete 37909-character E6 STATUS from immutable commit `3174ca0d7ecc087dbf306213ca19b4f3f55b9ee5`, followed by the E7 appendix exactly as preserved. Prior commits and all operator/review artifacts remain unchanged.
- This repairs the continuation document only. It does not change source, frozen intent, production, a lease, or runtime state. E7's host identity observations are historical and must not be treated as fresh host pins.
- On resumption, original PR2126 host receipts were read through the existing Emergent terminal: original verifier exit 0, verified true, 3 probes, lease closed and original status active false at 2026-11-25T15:34:11.304640+00:00. These are observed host receipts, not new verifier execution or a claim that our dashboard fix is published.
- Production subsequently advanced to PR2128 at `30c970807c05e291e2f48b35c84155b271fb5f09`. Old PR2122 remains preserved and must not be merged or published over that newer base. Next: integrate the exact reviewed dashboard source changes on the latest verified production base, run fresh integrated validation, build and freeze a new intent-only handoff; wait for the current publisher's original closure before using the shared host.


## E9 — Current production source, frozen B and verified evidence

Source was rebuilt on current production J4 `30c970807c05e291e2f48b35c84155b271fb5f09` without changing the ten reviewed application/test/workflow blobs. Current A is `0b39c456ea17d6e80f824f44b57563764441216c`; only its current status document differs from the prior own source. Fresh source integration, isolated backend regressions, original candidate validation and independent full source/intent review passed.

The frozen intent-only B is `ebaae98290f25db5580d888601d236431961d104`, tree `d50681f64d3387eb4b6f2137658be9cb11c62bd8`, RID `rg5-37ff4cc0a52caf321810a8dce1cb6c8f1c27dd8e2f9e5f0a57944ffc90efaf5a`. The original task branch and PR2129 successfully advanced to this existing B at 16:24:24 UTC on Nov25 after earlier GitHub incident failures; both heads were read back exactly. PR2122 is closed and superseded. Do not reuse its old frozen release.

The A4 application CI jobs passed. Final A4 check state was 29 success, 6 skipped and 2 CodeQL failures at analysis/SARIF upload. The failure cause is unproven; no security gate is waived. Fresh B checks and raw governed-output review are underway. This checkpoint is evidence preservation, not merge/deploy clearance.

Fresh backend selections passed: 300 payment tests, 106 inherited diagnostics tests and 451 four-platform tests plus 27 subtests. Selections overlap. The packet preserves raw XML, logs, exact commands/environment, independent audits, the original candidate ZIP, source manifest review, full CI logs/metadata and the minimal operator adaptation with six isolated test groups.

See `checkpoints/E9-verification-manifest.json` and its complete `E9-verification.zip` for hashes, source bindings, qualifications and exact remaining steps. Archived reports retain their original observation times. In particular, archived B-identity.json predates the successful ref update and is superseded by the E9 readback record.

Production has not changed by this task. No own lease, publish or host rehearsal has occurred. PR2128 published first and its original verification is still pending in the last reviewed proof. Preserve its lease. After all B gates/output reviews and predecessor closure, perform a normal tree-equivalent merge; require D gates, fresh envelope and actual original clone/app rehearsals before prepare/prepublish/one publication, three original probes and inactive status. Then verify Nov24 through the normal dashboard demand-refresh UI.

## V4 operator proposal: new release bindings and fresh observed host identities

Status: **NOT_EXECUTABLE_UNTIL_D_ENVELOPE_CI_AND_ROOT_REVIEW**.

A local proposal adapts the exact E8 v3 helper to J `30c970807c05e291e2f48b35c84155b271fb5f09`, A `0b39c456ea17d6e80f824f44b57563764441216c`, B `ebaae98290f25db5580d888601d236431961d104`, common tree `d50681f64d3387eb4b6f2137658be9cb11c62bd8`, and release ID `rg5-37ff4cc0a52caf321810a8dce1cb6c8f1c27dd8e2f9e5f0a57944ffc90efaf5a`.

The proposal changes exactly five release string literals and twelve LIVE_SCOPE integer literals. Fresh root metadata from 2026-11-25T16:19:09.342860+00:00 identifies PID 310/start 197045, with the same command hash, executable path and exact protected dbpath. Executable, three directory, and sibling-log device/inode pins are updated to the supplied observations. Modes, ownership, scope paths, policies and all control flow remain unchanged.

The host observation is 1,955 bytes, SHA256 `825209ecaac7f243fd5fe8b6b03d5b01dd3920d3a8ed478fb3e83b07de63a4e0`. It reports no guard call and no DB descendant reads; this reviewer did not access the host. It does not establish inactive-guard status, full host replacement, or continuous old-log prefix preservation. Root states PR2128 remains with its original publisher.

Final helper: 43,577 bytes, SHA256 `900cbe56a5d40e304c8975e559d4d4f77c5f4d9b0a9b77168d349d30d83a1780`. Independent AST-span reversal of the seventeen literals reproduces golden v3 bytes; full normalized AST equality passes. The unchanged six fixture groups pass against this exact helper (exit 0, 0.2217 seconds, empty stderr). The harness differs only in its expected helper hash; helper/harness hashes remain unchanged after testing.

Only fixture execution occurred: no actual main/rehearsal/adapter, host, database or Git/network invocation. D/START/count remain unset, no execution envelope exists, and no lease preparation/publication/service change occurred. Successful B/D CI, actual D, root independent review, a fresh exact envelope, predecessor publication/verification closure and inactive original guard remain prerequisites. This paragraph is a local evidence draft, not a remote update.


## E10 — Exact B gates and complete governed artifacts verified

PR2129 head remains `ebaae98290f25db5580d888601d236431961d104`, base `30c970807c05e291e2f48b35c84155b271fb5f09`, B tree `d50681f64d3387eb4b6f2137658be9cb11c62bd8`. It is now non-draft and mergeable clean. Final exact-B checks at 16:36 UTC: **36 success, 10 conditional skips, no failed, queued, running or neutral checks**. Both CodeQL language jobs and aggregate passed. No gate was waived, retried or changed for B.

Original Readiness `45653522820` passed on B, including both host Node20.20.2 and Node24.19.0 adapter rehearsals. Independent reviews checked full source/intent lineage, sanitized adapter evidence, original governed commands/toolchain, two builds, package proof and localhost route assertions. The original tracked B intent loader also passed in the clean local checkout.

Both original generated-output archives are preserved with their GitHub artifact digests under `checkpoints/`. Independent inspection verified every byte in the complete 387-file payload (383 frontend artifact files plus metadata, proof, backend identity and tracked-intent provenance), all paths/types/modes and exact metadata/proof/identity bindings. All 387 file bodies match between host archives; only archive containers differ. This closes the earlier A4 raw-asset review limitation. See `E10-B-verification-manifest.json` and `E10-B-review-evidence.zip` for complete proofs, original artifacts and limits.

Production is still unchanged by this task. PR2128 original verifier was observed running at16:35 UTC with no completion result; no guard/status/verifier was started by this task. Wait for its matching original successful three-probe result and inactive closure. Next: normal merge of exact B with unchanged production base, actual D checks, fresh host envelope, original protected clone/app rehearsals, prepare/prepublish, one publication, explicit success, original verification/inactive closure, then Nov24 dashboard demand-refresh acceptance. User authorization already covers the entire sequence.


## E11 — Normal merge D and exact-D gates complete; host handoff frozen

PR2129 merged normally as `b651eacf5ded173c4f3224d195ef874b8bcea312`; ordered parents are J `30c970807c05e291e2f48b35c84155b271fb5f09` and B `ebaae98290f25db5580d888601d236431961d104`. D tree equals B exactly. At16:55 UTC all eleven exact-D push checks and all three workflows completed successfully: Readiness45657345385, Security45657345179 and CodeQL45657345009. Both CodeQL languages and both host adapter rehearsals passed. No failed, skipped or pending D check remains.

PR2128 original verification completed three probes, exit0 and inactive status, preserved at immutable `1a06213567397bd3b4fa01001e25349c006e7572`. Root independently read original verify/status bytes and checked their hashes, then called the current original host guard at16:44:46UTC: inactive, exit0, empty stderr. Fresh16:47 host metadata matched every v4 pin, clean starting J and432 paths.

The exact ten-key envelope and one-shot runner are frozen with independent reviews and root review. The runner's initial journal error was fixed before any execution; three isolated error-recovery checks passed. The unchanged reviewed v4 helper retains all original guard/lock, source/pin/count, foreign-preservation, backup/capacity and two actual adapter gates. See `checkpoints/E11-D-host-handoff.json` and its archive for exact hashes and full evidence.

Only the helper file has been staged under `/tmp/mezan-pr2129-operator-o1l3bv1c`; no helper invocation, own lease or own publication has occurred at this checkpoint. Production Git changed through the authorized merge; live runtime has not changed by this task. Next: verify production still D, transfer exact envelope/runner, launch once, inspect original helper completion and receipts, then prepare/prepublish, one publish, explicit new success, original verify3/inactive closure, and Nov24 dashboard demand-refresh acceptance. Do not invoke concurrent guard status while the helper owns the original operation lock. User authorization already covers these actions.


## E12 — original host stop preserved; separate recovery required

Observed 2026-11-25 after the single helper invocation at 17:07:27 UTC. The original helper completed at 17:17:33 UTC with exit 2 and receipt BLOCKED. All 77 earlier command steps passed, including the complete owned-clone original adapter/validators, four generated-output backups, pre-FF capacity/preservation, and the fast-forward plus tracked-clean checks at exact D. Step78 (unchanged strict intent validation) rejected the inherited backend/__pycache__ directory. The canonical-app adapter did not start. No own lease or publication occurred.

The original receipt remains BLOCKED. The post-FF backup recheck, second capacity check, canonical adapter and final preservation were not reached. The original full foreign baseline and backups remain retained on the host, not exported. Both archived evidence sets and all four backup manifests received independent review, including bounded public-content review.

Read-only full Backend inventory found exactly one inherited cache directory with two files totaling120563 bytes, all matching the original foreign baseline. No cache move has occurred. Both the initial resolved-filename comparison and the later recorded-filename comparison are preserved; no cache code was executed and no original validator rule is relaxed.

A separate narrowly reviewed D-start preservation recovery is being prepared. It must preserve the exact cache via an exclusive same-filesystem move, retain all other foreign-state equality and existing append policies, recheck source/remote/lease/live scope/backups/capacity, then run the unchanged strict validator, canonical adapter and final proofs. Do not rerun v4 from D, reset the checkout, delete caches, waive bytecode validation, or relabel partial proof. Publication and live dashboard acceptance remain pending.

Evidence: checkpoints/PR2129-HOST-BLOCKED.zip; checkpoints/PR2129-CACHE-OBSERVATIONS.zip; checkpoints/E12-host-blocked-independent-review.json; checkpoints/E12-host-blocked.json.


## E13 — separate recovery operator frozen; actual recovery pending

The original one-shot host attempt remains **BLOCKED** at step78 after the fast-forward to exact D. Its receipt, wrapper completion, logs, four backups, original foreign-before baseline and E12 evidence are retained unchanged. The canonical-app adapter has not run. No own release lease or UI publication has occurred.

This checkpoint freezes a separately reviewed recovery that starts at D `b651eacf5ded173c4f3224d195ef874b8bcea312`. It requires the original failed-attempt files and foreign-before hash, unchanged source/remote/lease/live-process identities, original generated-output backups and measured current capacity. Its only foreign-state relocation is the exact three-record `backend/__pycache__` mapping: one directory and the two retained files move together through Linux same-filesystem `renameat2(RENAME_NOREPLACE)` into a private owned Git metadata directory. The original full baseline remains authoritative; all other equality and already-reviewed append rules remain unchanged. No cache deletion, content substitution, validator waiver, reset, automatic retry or automatic restore is introduced.

After the separately journaled move, the unchanged strict intent validator and canonical adapter must pass, followed by the full frontend/package/runtime/proof checks, retained-backup verification and mapped foreign preservation. The recovery has not run at this checkpoint. Static reviews and eight focused synthetic fixture groups do not establish actual host readiness, adapter success or publication success.

The recovery runner preserves the original one-shot durable journal design with separate state/output filenames. The original guard phase runner is also frozen for the later status, prepare, prepublish, verify and final status sequence. Its phase selection does not enforce ordering; root must inspect every original result and must never blindly repeat a phase whose child may still be running. Earlier draft labels remain in their original review records, with final approval bound to exact frozen helper and runner bytes by the root review.

Evidence: `checkpoints/E13-recovery-static-review.zip`, `checkpoints/E13-recovery-static-review.json` and the three direct operator files under `operators/`. The complete E12 STATUS prefix is preserved. Full foreign inventories, raw backups, bytecode, database contents, environment values and private business records remain excluded.

Next: read back durable E13, stage exact reviewed bytes and launch the separate recovery once. Require actual recovery PASS before original inactive status, prepare and fresh prepublish; then one UI publication, a newer explicit platform success, the original three-round verifier and inactive closure. Finally verify Nov24 through the normal dashboard demand-refresh UI. Existing user authorization covers these actions; publication and live dashboard acceptance remain pending.


## E14 — Explicit current-host boundary; canonical rebuild pending

E13 remains the durable static review checkpoint at `89910f93f43c644f2f86409d19acc1ef87610043`, tree `324d321a04de860f819ae0c97f0657d47454d181`. This checkpoint appends a separate current-host boundary after the loss of prior temporary execution evidence. The reviewed source A, intent B, deployment D, tree and deterministic release ID are unchanged.

### Previous attempts retain their actual outcomes

- The original host rehearsal remains **BLOCKED**, exit 2, after its step-78 strict validator failure. Its E12 evidence is unchanged.
- The separately reviewed bounded recovery was launched once at 17:54:30 UTC on 2026-11-25. The last observed receipt at 17:56:52 UTC was **RUNNING** with 54 recorded commands. The exact cache directory and two files were **PRESERVED**, the original strict intent validator passed, and the mapped current-app foreign comparison passed for 185030 records with 3 original records retained separately.
- That observation does not prove the later canonical build, final preservation comparison or recovery completion. The interrupted recovery's final status is now **UNKNOWN** because its final receipt is unavailable.

### Current observations and concrete rebuild requirement

Root's code-server observations at 18:53–18:58 UTC found that the old control directory, both temporary stages and the original temporary backup stage were absent; both original recovery PIDs were absent. The cause of the missing paths is not established. `/app` now reports device 66345 rather than 66329, while the observed retained inodes remain consistent. This is an explicitly new boundary, not a claim of continuous old-host preservation.

The production checkout remained exact D `b651eacf5ded173c4f3224d195ef874b8bcea312` on `hotfix/prod-snap-meta-final` with clean tracked files. The original guard bytes and reviewed intent bytes matched their frozen hashes; the unchanged original `status` returned exit 0, empty stderr and `{"active":false}`.

All three required generated artifacts were absent: Frontend build metadata, reproducibility proof, and Backend runtime identity. The surviving `frontend/node_modules` contained exactly 4298 empty directories, with a measured 17649664-byte copy/allocation footprint. The latest measured app free space was 1065492480 bytes with 244682 available inodes. These observations explain why current read-only validators alone cannot establish readiness: a new canonical adapter build is required. Actual capacity checks must still be fresh at execution.

The prior cache quarantine remains in place under `.git`. Its two file bodies, lengths, inodes and pinned mtimes match the retained records, and both old manifests remain byte-identical. The changed device is explicitly recorded. Present quarantine equality does not reconstruct the lost historical full foreign inventory or prove a final historical preservation pass.

The management UI still showed **100th publish `bbaba13`**, last finished 2026-11-25 at 19:21 Riyadh. This task has made no publication attempt and has created no lease.

### Static current-host checkpoint

This packet binds the root observations, the exact new current-host helper and one-shot capture runner, the adapted original guard capture runner, the current execution envelope, focused checks, author review, independent review and root review. Final immutable hashes and review decisions are in `checkpoints/E14-current-host-static-review.json` and the archive manifest. The three direct operators and their execution envelope use separate `operators/host2/` paths; older operator bytes remain unchanged.

The new attempt has **not run** at this checkpoint. It uses a fresh current-attempt foreign baseline and current generated-output backups; it cannot substitute them for the lost earlier baseline or backups. Small journals, receipts and retained backups are assigned to a new private directory under `.git`; owned build cache and temporary work use a separate `/tmp` area. The existing quarantine is retained in place. No old-helper rerun, cache restoration, source fast-forward, lease creation, publication or business-data operation is asserted by this static package.

The durably recorded clean-clone rehearsal and exact-release CI remain valid for unchanged A/B/D. A second clone build is not being claimed or required merely because its old temporary directory disappeared. Current required artifacts still must be produced and validated by the unchanged canonical adapter and original verifiers.

### Exact next sequence

After root verifies the durable E14 ref and staged bytes, execute the reviewed current-host attempt once. Require original inactive status, current D/remote D and lineage, current backups and capacity, unchanged strict validation, canonical adapter, full identity/proof/package verification, and preservation for the new interval. Checkpoint the completed evidence immediately. Only then run original prepare, immediately-before-publish prepublish, one authorized UI publication, a newer explicit deployment success, original three-round verify, final inactive status, and normal Nov24 dashboard acceptance.

Production publication: **no**. Current release readiness: **BLOCKED pending actual host2 result**. Original first attempt: **BLOCKED**. Interrupted bounded recovery final result: **UNKNOWN**. Historical final foreign continuity: **not proved**.

### Frozen execution inputs

- `pr2129_fresh_d_rehearsal.py`: 51504 bytes, SHA256 `cfa3dc0adf6858165a3562b3dad2a9679177112718a5d58c1a1ff9188b164ff8`.
- `run-reviewed-host2.py`: 3915 bytes, SHA256 `dcaad9e5bcac0ab8a61f4db003dba26aa31f4d56c67e6f3b31610d59171bba84`.
- `run-original-guard-host2-phase.py`: 4732 bytes, SHA256 `cdd43c497014e4148b5e5e95b620e3fe6162f4f540bbf16a8115bba238d46ca4`.
- `execution-envelope.json`: 1592 bytes, SHA256 `a5bc2caf6faea02550877efa0c73320d1ee83366241a44fb399d4d222819f5a7`.

Independent review: `APPROVED_FOR_ONE_FRESH_CURRENT_HOST_REHEARSAL`; root review: `PASS_STATIC_REVIEW_ONLY`; three focused synthetic groups passed. These are static approvals for one current-host attempt, not host completion.
