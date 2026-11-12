# Google dashboard and store-courier release evidence

## Current state: deployed and verified; live acceptance open

PR [1887](https://github.com/AMASI-SA/AMASI-SA/pull/1887) is merged. Reviewed
source A, intent-only B, exact-B CI, the actual shared adapter rehearsal, and
this task's own `prepare`/`prepublish` have passed. The release owner clicked
the final **Publish changes** control once at about **2026-11-11 23:42 UTC**.
The newer green timeline then displayed exactly **`Deployment finished Nov 12, 02:53 AM`**
(Riyadh), and current published version became **`0ef7c77`**, replacing
`7577839`. The official guard subsequently returned **`verified:true`, `checks:3`**
for the immutable identities below; a fresh status returned **`active:false`**.
Production changed: **yes, deployed and verified**. Courier and Google live
acceptance remain **OPEN** for the concrete gaps below; no successful courier
preview or physical-paper printing is claimed.

This documentation is prepared separately on
`codex/google-courier-release-evidence-20261112`, based on the merge below.
**The documentation branch must never be used as a deployment candidate.**
The reviewed source/intent pair remains immutable. Root owns the documentation
checkpoint, continuation ledger, and the remaining live acceptance.

## Immutable identities

| Identity | Exact value |
| --- | --- |
| Reviewed production base J | `245a0beccbdbc2585a3ec2214e808eeef3734b57` |
| Source A | `b01341f95f1a1caf3a8741de09f52cb52f6536d9` |
| Source A tree | `e12e5f5e1fed2474daeb3cdabf9c4f751ae757e5` |
| Intent-only B | `b237fca03fb22e627ab5451b2c14c0f1a17f42b5` |
| Deployment merge | `803e87f72d9661f9b961587bbc0cededfd1f3c90` |
| B and merge tree | `ac5225a4cb70b150ee8383305fb5b5da3c3a8a25` |
| Reviewed runtime release | `rg5-e71b3c05b2a27483d7bea11daf03c82f7b80f67e4b9282b5ceb02664a9c95710` |
| Intent SHA256, 1,268,689 bytes | `f9677a1b95e50acfd9defddc9529893147bc92604af3465dc3af466b5b7ef89c` |

A is retained as the runtime `source_git_sha`; the tree-equivalent merge is
the operational `deployment_git_sha`. A to B changes only
`release/release-intent-v5.json`. The merge parents are J and B, and its tree
was independently compared with B before this documentation worktree was made.

## Reviewed build and artifact contract

The exact-A local governed bootstrap build completed with exit 0 in 21.93s;
exact-A verification also passed. The local freeze and CI Readiness
[43118295273](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43118295273),
artifact `13898284804`, independently passed the official candidate validator.
Their intent bytes were identical. The full manifests bind 1,175 backend
runtime files, 1,149 frontend source files, and 273 release-control files,
including the Google reader and all courier recovery changes.

| Bound build property | Value |
| --- | --- |
| Public client origin | `https://mezansalla.com` |
| Origin value SHA256 | `d2e055b74392bfdfcf1d64af1e88a91ab7029a49729a7ac53870bf48bfe4e5ff` |
| Governed toolchain | Node `22.23.2`, Yarn `1.22.22`, Vite `8.2.4` |
| Frontend artifact tree SHA256 | `ab6e7d8994e8a5e32bb2843225bd156c34996798bb1ee780a5643ee35c535325` |
| `build-meta.json` SHA256 | `b37d61315c9e6ee03f58e694689450fc7496ec888a0a2a51003bbd3a68fa1f98` |
| Two-clean-build proof SHA256 | `8b417a862c7b455db22ec82593c08ccd6cadee9e941c6bbc1e702ef8b048ffa5` |

Independent source, history-recovery, recipient/gift, race and candidate reviews
reported no unresolved blocker. Local mock tests were not substituted for the
required isolated-Mongo CI. Earlier source checkpoints and failed bootstrap
attempts remain historical evidence only.

## Exact-B CI evidence

The final query for B returned **25 successful workflow runs**, no failed or
pending runs, and one unrelated **TikTok Creator Release Candidate** skip.
That workflow's job explicitly requires the separate
`codex/tiktok-content-ci-20261011` branch; its skip is not a skipped acceptance
test for this release. Some successful workflow names occur in more than one
run, so 25 is the run count, not a count of distinct workflow definitions.

| Gate | Final evidence |
| --- | --- |
| [Runtime adoption 43119023502](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43119023502) | Reference/baseline/runtime each contain 164 matching identities. Each execution arm: 40 passed, 62 deliberately deselected. Regression: 218 passed plus 5 subtests. JSON comparison, every raw read-contract audit, and XML zero-skips/failures/errors gate passed. |
| [Operational Balance 43119020534](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43119020534) | Backend 3158 passed; shipping-summary/calculator 16 passed plus 18 subtests; frontend 40 suites and 462 tests passed. Missing/skipped-test guards passed. |
| [Stock Eligibility 43119020822](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43119020822) | 3067 passed plus 7 subtests on isolated Mongo. Terminal guard: 3067 executed cases, zero skips/failures/errors. |
| [Shipping Print Boundary 43119020215](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43119020215) | 26 Mongo latency cases, 120 courier cases, and 596 broader cases passed. One intentional memory-only transaction-rollback skip remains; its real-Mongo counterpart passed. |
| [Review Completion 43119019910](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43119019910) | Android 312 plus 59 subtests; provider/local 658 plus 345 subtests; 41 stranded cases and 1 acceptance case. XML zero-skip guard passed. |
| [Release Readiness 43119019606](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43119019606) | Exact-B reviewed-intent and adapter checks passed. |

Runtime artifact `13899509479` is 1,075,941 bytes, SHA256
`9b7bbcee5929e2315a204a5905a9bbed244f38b18dfc5f89157c365f7bfbce32`;
its recorded head is B. Stock artifact `13900890093` was uploaded successfully.
A's Runtime and Balance also passed; A's Stock run was cancelled when B arrived.
The completed Stock result above is B's own result, not a reused A result.

## Actual shared rehearsal and publish handoff

The release operator recorded the following in
[Issue 1006](https://github.com/AMASI-SA/AMASI-SA/issues/1006); the latest
verification/acceptance entry was read at ledger update 2026-11-12T00:07:01Z:

1. Shared source fast-forwarded to the exact deployment merge with no tracked
   differences. All **120 untracked platform files** were preserved. Financial
   and Android work remained separate.
2. The first actual adapter rehearsal correctly refused two ignored generated
   `.pyc` cache files. They were confirmed untracked, ignored and bytecode-only,
   then reversibly quarantined outside the shared workspace. No source or
   platform file was deleted, and the official guard remained inactive.
3. The second configured `yarn build` completed **exit 0 in 93.16s** on the actual
   Linux aarch64 host (Node 24.19.0, Python 3.11.18, Yarn 1.22.22). The adapter
   used the separately governed toolchain above. Artifact/proof checks and
   isolated package checks passed with the reviewed source and runtime identity.
4. At **2026-11-11T23:42:38.216Z**, this task's own `prepare` and `prepublish`
   succeeded for actor `codex-google-courier-print-20261112`; `prepublish`
   returned `ready_to_publish:true` with the identities above. The owner then
   clicked Republish and the final Publish changes control once. The UI showed
   publishing disabled/in progress.
5. The newer green timeline displayed **`Deployment finished Nov 12, 02:53 AM`**
   (Riyadh), with current version `0ef7c77`. This is the exact observed UI wording;
   no literal `Deployment Succeeded` text was observed.
6. The official shared v5 verification completed with `verified:true`, `checks:3`,
   source A `b01341f95f1a1caf3a8741de09f52cb52f6536d9`, deployment
   `803e87f72d9661f9b961587bbc0cededfd1f3c90`, and runtime
   `rg5-e71b3c05b2a27483d7bea11daf03c82f7b80f67e4b9282b5ceb02664a9c95710`.
   Boot time was `2026-11-11T23:53:25.260197+00:00`; the frontend artifact matched
   `ab6e7d8994e8a5e32bb2843225bd156c34996798bb1ee780a5643ee35c535325`.
   All three official probes covered canonical/cache-busted governed JSON/MIME,
   frontend bytes/proof, and backend identity/critical-source checks.
   Subsequent official status returned **`active:false`**; this task's release
   lease is closed.

## Live acceptance remains open

### Courier: historical label recovery still rejects a native empty alias

An authorized post-publication current-label trial returned:
`تعذّر إثبات المندوب والعنوان الحاليين من سجل سلة؛ لم تُفتح الشحنة المستبعدة.`
Printing remained disabled, with the complete-and-issue action offered. This is
a failed preview acceptance, not a successful print. Subsequent complete provider
reads identified a native optional `external_company_name` present as exactly
empty string. The deployed `_child` check treats a non-None alias as populated
and strict text parsing rejects that empty value. A narrow follow-up is being
prepared separately; it is not included in the deployed A/B pair. Canonical and
superseded source data must remain unchanged, and nonempty contradictory aliases,
recipient/gift checks and final fences must still reject.

### Google: positive saved subtotal exists, but the selected portfolio is incomplete

For the explicit **2026-11-11** range, the live saved platform response has
`connected:true`, `hourly_available:true`, native hourly source,
`known_total_sar:96.48`, `total_sar:null`, `account_count:3`, and the sole reason
`google_native_account_day_missing`. The working account is SAR/Asia-Riyadh.
Saved sync logs report three accounts attempted, one complete, two rows saved
and two errors: one account requires reauthorization (`google_ads_needs_reauth`),
and one is a manager requiring client selection
(`google_ads_manager_account_requires_client_selection`). Neither missing account
is a proven zero. The reader correctly retains 96.48 SAR as a known subtotal
without certifying the full expense or profit.

The yellow card nevertheless displays `لا بيانات`, and the executive Google
spend cell displays `—`. The frontend normalizer currently retains partial
quality/known fields only for Snapchat; the card likewise has a Snapchat-only
partial display. A proposed frontend-only follow-up preserves Google quality and
the positive known amount, passes same-period evidence to the executive table,
and displays `المتاح 96.48 · غير مكتمل` solely as an incomplete spend annotation.
It must leave backend monetary fields, full totals, profit, ROAS/CPA, account
selection and financial merge guards unchanged. No refresh/sync, reauthorization,
manager exclusion or provider write was triggered by these read-only checks.

## Remaining acceptance and next safe action

- Finish and independently review the separate narrow courier/Google-display
  follow-up, with current production/ref and serial-release coordination checks.
- Any new runtime change requires its own fresh source A, reviewed intent-only B,
  exact-source CI/build evidence and governed release. Do not republish this
  unchanged pair as a remedy or deploy the documentation branch.
- After that follow-up, repeat the already-authorized live courier previews and
  same-period Google display acceptance. The full Google portfolio remains
  incomplete until its separate access/client-selection gaps are resolved with
  appropriate evidence; displaying the known subtotal does not resolve them.
- Root then checkpoints the documentation and updates the shared ledger without
  changing this immutable release. Coordinate subsequent release ownership from
  the now-inactive guard and preserve financial/Android work.

No live order identifiers, customer data, credentials, or secrets are retained
in this document. Pending items may be updated only from actual new evidence.
