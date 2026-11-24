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
