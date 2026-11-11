> **Evidence-only checkpoint — do not deploy this branch.**
> Deployable candidate is intent-only B `3949adc402ff828d2e15b2d53d8daab54bf821bc` on
> `codex/supplier-receiving-recovery-r2-20261111`, with final CI passed and owner authorization now granted for merge/publication.
> Follow the latest execution checkpoint below; no repeat approval is needed. This post-intent document is continuity evidence.

# Supplier receiving pending-scan regression recovery

## Scope and source

Initial base: `3f60c1a08263ff1eb2412c0f9224467a198ac278`.
Reviewed integration base: `2d6d74806ab32333a1de1cfc8b8c73c3e434a851`.
Task branch: `codex/supplier-receiving-recovery-r2-20261111`.

This restores the reviewed source behavior from
`d1c2a3268e81ef6305cf853a66e7c7e1e855082f` (PR #1809) and
`71d5d48a11078c57dcbdcb27a10b90a27f3625c1` (PR #1837) on the current base.
No historical release intent is copied.

The production source had reverted to a scan reconciler that could fail an
entire sweep on a legacy pending row without a token. Expired valid attempts
then left refresh and cancellation returning `supplier_receiving_scans_pending`.
These mechanisms were reproduced against the base with synthetic data.

## Behavior

- The existing sweeper expires only attempts with valid date and fencing
  evidence, and checks expiry again during the atomic state transition.
- Authorized POST refresh settles that session's expired valid attempts inside
  its existing transaction, preserving already committed receipt pieces.
- Explicit cancellation claims the session and fences all its pending attempts
  in the same transaction as the existing draft rollback. A racing scan either
  commits first and is included in rollback, or loses its write fence.
- GET session and scan-outcome recovery remain read-only.
- A newer lock or another session's attempt is not cleared by old-attempt expiry.
- Close interprets Mongo's timezone-naive UTC lock expiry correctly instead of
  comparing it directly to an aware datetime and returning a misleading 503.
  Present locks with missing or invalid expiry continue to block finalization.
- Malformed attempts without expiry evidence are not silently expired by
  refresh. Explicit cancellation can fence them; preserving such a session may
  require further evidence. No direct production repair is included here.

## Verification

The combined expiry/cancellation restoration passed **181 tests, 0 failed,
0 errors, 0 skipped**, pytest exit **0**, duration **65.07 seconds**, against an
isolated MongoDB **7.0.17** replica set. After the final close-date correction,
the affected approval, scan-lock and financial-integrity suites passed afresh:
**73 passed, 0 failed, 0 errors, 0 skipped**, exit **0**, **52.45 seconds**.
The supplier scan CI workflow pins MongoDB **7.0.16**. The first local runner
banner incorrectly named another workflow's version; its banner was corrected
after inspecting `.github/workflows/build20-supplier-scan-recovery.yml`.

The 12 suites were:

```text
test_supplier_optional_unstarted_receipt.py
test_supplier_scan_recovery.py
test_supplier_scan_lock.py
test_supplier_scan_approval.py
test_supplier_receiving.py
test_supplier_invoice_financial_integrity.py
test_supplier_multi_supplier_lifecycle.py
test_supplier_invoice_live_cost_session.py
test_supplier_invoice_service_eligibility.py
test_supplier_refresh_real_mongo.py
test_supplier_refresh_wrapper.py
test_supplier_receiving_concurrency.py
```

The new regression checks prove that refreshing a stale session with **19**
confirmed synthetic pieces retains all 19 exact piece identities and its count,
with no invoice or general-ledger write. Reading an expired pending outcome is
verified not to mutate its saved attempt. Additional checks cover valid expiry
despite a malformed foreign-session row, a newer lock, active-scan cancellation,
foreign-employee denial and both orders of the scan/cancellation commit race.

Baseline checks failed for the intended defects: missing-token sweep exception,
expired refresh/cancel returning 409, and active cancellation returning 409.
An initial broad run had 48 fixture setup errors from an omitted isolated
invoice Mongo URI. The local runner was corrected and the complete 181-case
suite then passed. Two newly added assertions initially assumed cancellation
zeroes the historical scan count; they were corrected to verify the existing
`cancelled_piece_count` audit contract. No runtime behavior was changed for
those assertions.

`git diff --check` and Python compilation of the three changed Python files
passed initially; both checks were repeated successfully for the final four
Python files. Evidence is retained locally in `.test-runtime/supplier-regression.log`
and `.test-runtime/supplier-regression.xml`, and final-source evidence is in
`.test-runtime/supplier-final-approval.log` and
`.test-runtime/supplier-final-approval.xml`, pending the coordinator's durable
checkpoint.

The new combined acceptance test covers 19 confirmed pieces and a valid expired
pending attempt: close blocks with 409, authorized refresh preserves all 19,
close creates one invoice for 39,900 halalas, replay returns that same invoice,
and cancellation after close rejects with 409 while preserving the invoice,
two ledger entries and session. This test exposed the previously untested
timezone-naive lock comparison: before correction, close returned 503. Separate
baseline checks also proved active and invalid expiry returned 503, while a
missing expiry incorrectly permitted close. The final comparison handles all
three as 409 without financial writes. All these tests passed in the final
73-test run; no live invoice was created.

## Handoff boundary

The source is checkpointed remotely in draft PR #1882. No production database
change, production merge, tracked release intent update, release lease or
deployment has been performed for this task. CI built the source candidate.
The live affected session has not been claimed recovered by these synthetic
tests. The coordinator owns release preparation and live readback.

## Integration review and next action

The independent source review accepted the authorization, transaction fencing,
19-piece preservation, read-only GET and closed-invoice invariants. The root
coordinator independently checked the complete diff and confirmed that all
three original affected files are identical between the observed deployed
source `5843ff899ce8cc814ba60de1e44bb69ec732f4c4` and the task base above.
The root coordinator then independently reran the five approval checks on the
final source with a fresh disposable replica set: **5 passed**, exit **0**, in
**5.37 seconds**, including the complete 19-piece recovery/close/replay path.

While this source was being checkpointed, production advanced through reviewed
PR #1881 to `77e687e6964254617b4aa9eb0e3164cc968cff3b`. The first source
checkpoint `3fc4e6902e05f1b1b8ada60b82111e300a0e7ad4` therefore failed the
release-readiness ancestry check; its PR base was newer than its ancestry.
The latest production was integrated cleanly without changing any of the four
supplier Python files. The root repeated the five approval checks on that
integrated source: **5 passed**, exit **0**, **5.64 seconds**. All reviewed
Qoyod/campaign changes remain intact. This integration changes only the task
branch; it does not merge the supplier fix to production.

Run the affected final-source suite with the repository's disposable replica-set
setup from `.github/workflows/build20-supplier-scan-recovery.yml`:

```bash
cd backend
python -m pytest tests/test_supplier_scan_approval.py tests/test_supplier_scan_lock.py tests/test_supplier_invoice_financial_integrity.py -q
```

The broader supplier regression command is the same workflow's supplier/invoice
test step, with `test_supplier_refresh_real_mongo.py`,
`test_supplier_refresh_wrapper.py` and `test_supplier_receiving_concurrency.py`
included as documented above. Only synthetic local test data was used.

This is a source candidate for a new reviewed v5 release. Before a release,
recheck the production branch and shared guard, integrate any newer reviewed
production changes without restoring the old release intents, run the required
readiness gates, freeze a fresh intent-only commit, and follow `AGENTS.md`.
Publication and recovery of the employee's live session remain unverified.
Do not cancel a live draft or approve its invoice as a test.

## Source gate correction

The integrated task history was preserved on
`codex/supplier-receiving-recovery-r2-checkpoint-20261111`. Source
`35dd1ebbd895e19b9de4ff175cf2c89015d99847` uses the identical tested tree
`a8e8bbb68257d53cda1cfef2b7ba25c7035ddb40` as a direct child of the reviewed
production base. Its v5 readiness build succeeded; no intent-only B was frozen.

Of its 32 CI checks, 25 succeeded, six were scope/release-stage skips and one
failed. The failed `display-mongo` run had **163 passed and one failed**: its
financial-boundary source hash correctly noticed the deliberate close-lock
expiry precondition correction. Scan recovery, invoice integrity, native
contract, backend preflight and the frontend build all succeeded.

The follow-up changes only this handoff and the financial-boundary test. The
test asserts the exact new expiry block once before normalizing that block to
the previously reviewed text, following the existing index-gate normalization.
All original function and file hashes are retained; authorization, invoice
construction, posting, service linkage and other financial code remain pinned.
The existing functional checks independently exercise active, expired, invalid
and missing lock dates, with no financial write for blocked close attempts.
The corrected boundary test passed locally: **1 passed**, exit **0**,
**0.62 seconds**. Runtime source is unchanged from the reviewed 35dd checkpoint.
Independent review accepted the correction and checked three in-memory mutants:
reversing the expiry comparison failed the exact-block assertion, while changing
the close fence or expected invoice total failed the original function hash.
No files or financial data were mutated for that review.

The intent artifact for 35dd is historical evidence only and must not be used
after this source follow-up. The subsequent integration below supersedes both
35dd and f4 deployment candidates. Production publication requires separate
user authorization.

## Latest production integration

The source checkpoint `f4c37c87152bfe6e66a165404abac1c728bc1674` completed all
**32 CI checks: 26 succeeded and six were expected scope/release-stage skips**.
The GitHub check-runs response was independently read back after completion;
there were no failures or pending checks. In particular,
[scan recovery](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43056122552/job/130645934376),
[invoice integrity](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43056122492/job/130645934640),
[display Mongo including the corrected financial boundary](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43056122820/job/130645935467),
native contract, backend preflight and the frontend build all succeeded. The
checkpoint remains archived on
`codex/supplier-receiving-recovery-r2-tested-f4c37c87`.

While those checks ran, reviewed PR #1879 advanced production to
`2d6d74806ab32333a1de1cfc8b8c73c3e434a851` (tree
`f95ddb857df15be7ae10aeb9b5135f1c06862716`). A new isolated checkout starts at
that exact commit and applies only the six task files from 77e through f4: two
backend modules, three supplier tests and this handoff. The five Python files
are byte-identical to f4. There is no file overlap with PR #1879; its option-cost
visibility changes and PR #1881's Qoyod/campaign changes are preserved. The
production release intent remains untouched; no historical intent is copied.

On this integrated source, the five existing approval cases and the single
financial-boundary guard passed together on a fresh isolated MongoDB **7.0.17**
replica set: **6 passed, 0 failed, 0 errors, 0 skipped**, pytest exit **0**, in
**7.22 seconds**. This includes the 19-piece expired-attempt refresh, single
invoice, replay and closed-cancellation acceptance case, plus active, missing
and invalid close-lock expiry cases. No runtime or test implementation changed
for this integration. The exact command was:

```bash
/workspace/scratch/81f2839b514c/courier-test-env/bin/python .test-runtime/run_with_mongo.py backend/tests/test_supplier_scan_approval.py backend/tests/test_supplier_display_financial_boundary.py::test_approved_close_posting_and_service_linkage_are_unchanged -q --junitxml=.test-runtime/supplier-integrated-acceptance.xml
```

Local evidence is in `.test-runtime/supplier-integrated-acceptance.log` and
`.test-runtime/supplier-integrated-acceptance.xml`. The five source/test file
identity checks and `git diff --check` passed. Only these six tracked task files
are staged for the coordinator's new source checkpoint; there is no commit,
remote write, release lease or production mutation from this integration.

The successful f4 CI and its build artifacts describe that older source only;
**the 35dd and f4 release artifacts are not deployable for this integrated
candidate**. Next, create a new source A directly on the current production
base, run its required CI/readiness gates, obtain its exact-source v5 candidate,
freeze a fresh intent-only B and verify both clean-clone adapter rehearsals.
Recheck production ancestry before release. The live affected session remains
unread and has not been claimed recovered.


## Reviewed intent checkpoint

Source A: `32919f806d17c9fd9d466b368abf807bd40ee925` (tree `aefc95c9dd5daf896d7c4a62ef4d4b82891136ab`).
Reviewed production base J: `2d6d74806ab32333a1de1cfc8b8c73c3e434a851`.
Intent-only B: `3949adc402ff828d2e15b2d53d8daab54bf821bc` (tree `d696d29e828044c808778e95a20e48c3fb93f921`).
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1882.

All source-A checks completed: **32 total, 26 success, six expected scope/stage
skips**, with zero failures, pending or neutral checks. The production branch
still matched J immediately before B was frozen. B has A as its sole parent
and changes only `release/release-intent-v5.json`.

The exact artifact came from source readiness run
[43058175802](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43058175802),
artifact **13873100762**. The downloaded ZIP SHA-256 matches GitHub metadata:
`654fee8f3440e7a1de5d54043180247d6f4a807566917f771d49ee95a34b1a3a`.
The intent is **1,264,209 bytes**, SHA-256
`a4a6c018b9f7bc06bf7234ab4a6c2cb1678788a07e839341e137a98f50d73b31`, Git blob
`2960c61c2d51801880f2019ea826a7fa1e940bcd`. Its runtime identity is
`rg5-fe87f7bb866db60c49025385479a29080e8f34fc02aef92e7bb395d50c3385b1`.

Full candidate validation matched independently derived Git manifests: 1171
backend runtime files and 272 release-control files. Independent clean-clone
B validation then passed (exit 0): `load_release_intent`,
`verify_release_intent_git`, sole-parent and only-intent diff, exact tracked
bytes and artifact provenance. No new local build or workflow dispatch was
needed. CI performs the actual adapter rehearsals.

B readiness run
[43059200630](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43059200630)
classified B correctly. Frontend double build, backend preflight and host
compatibility checks passed. The actual adapter rehearsals are running under
host Node 20.20.2 (job 130657410463) and Node 24.19.0 (job 130657410505).
Exact-B CI subsequently completed successfully; the final handoff below records
the complete result. No checkpoint is production publication authorization.

**Production changed by this task: no.** PR is ready for review and unmerged. There
has been no shared `/app` edit, owned release lease, deployment, APK update or
live invoice/session mutation. The affected employee session remains unread
and unverified. No old 35dd/f4 artifact may be deployed.

Next: finish exact-B checks and both adapter rehearsals, recheck production
base, and leave the final evidence checkpoint. Ask the user for separate
production merge/publication authorization required by `AGENTS.md` before
those actions. On authorization, use the shared guard only after observing
`active:false`; preserve other owners' leases and files, publish once, require
new explicit Deployment Succeeded and official three-probe verification, then
verify continuation from the original employee session. Do not cancel the
live receipt or approve an invoice as a test.


## Final handoff — ready for separate publication approval

Verified at **2026-11-11 16:40 UTC**. PR #1882 is **ready for review, open,
unmerged and mergeable**. The deployable task branch remains exactly
`3949adc402ff828d2e15b2d53d8daab54bf821bc`; A and B were not rewritten.
The reviewed production branch still points to
`2d6d74806ab32333a1de1cfc8b8c73c3e434a851`.

All **34 exact-B checks** completed: **28 success and six expected skips**,
with **zero failures, pending checks or neutral results**. Both CodeQL analyses
and their aggregate completed successfully. The six skips are four unrelated
advertising-scope jobs, the TikTok candidate job explicitly restricted to a
different task branch, and the production-only manual redeploy handoff. No
receiving, invoice, stock, financial-boundary or required release check was
skipped. Readiness run **43059200630**, attempt 1, completed **SUCCESS**.

Both real adapter rehearsals passed all steps, including adapter execution,
artifact and package verification, and governed runtime JSON checks:

- [Host Node 20.20.2 — job 130657410463](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43059200630/job/130657410463)
- [Host Node 24.19.0 — job 130657410505](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43059200630/job/130657410505)

The existing published Android 76 source at
`AMASI-SA/amasi-mobile@3f0567991c20c1b229811ceb8b852d8bb040bf6c` was also
reviewed: Continue mounts the compact receiving screen, whose load/refocus
path calls the existing authorized POST session-refresh endpoint. This fix
changes only the server and requires no new APK. The affected device's actual
installed build and its live pending-attempt rows were not inspected. The web
catalog-refresh button remains GET-based; its valid expired attempts can
recover through the repaired background reconciler. Malformed attempts without
expiry proof deliberately require explicit cancellation or further evidence
before a preserving repair can be designed.

**Production changed by this task: NO.** There was no production merge,
publication, shared `/app` modification, owned lease or live session/invoice
mutation. No claim is made that the live affected receipt has recovered.
Unrelated #1879/#1881 code and other conversations' work were preserved.

The code, reviewed intent, CI and rehearsal preparation are complete. The only
next user decision is authorization to merge PR #1882 and publish it under the
repository's v5 protocol. After authorization, first fetch current production
and verify that the reviewed base remains compatible; check the shared guard
and proceed only with `active:false`. Preserve all foreign leases and files.
Use the frozen B or its tree-equivalent normal merge, never this evidence-only
branch. Complete the governed build/own prepare/prepublish, publish once, then
require a newer explicit Deployment Succeeded and official three-probe verify
with the intended runtime/source and an inactive guard. Verify continuation
from the original employee session afterward, preserving confirmed pieces;
do not approve the invoice or cancel the receipt as a test.

Local detailed evidence is under `release-evidence/3949adc4/`:
`B-validation.json`, `B-rehearsals.json` and `B-ci-proof.json`. Durable proof is
also in the immutable A/B commits, the linked CI run/jobs, PR #1882 and Issue
#1006. Re-read this final handoff and the live branch/PR state before resuming.


## Authorized publication execution — 2026-11-11 17:12:22 UTC

The owner explicitly instructed to merge, publish and continue now. This
supersedes every earlier approval-wait statement for this release. The frozen
A/B pair is unchanged. Fresh B checks now total **40 completed: 30 success and
10 expected scope/stage skips**, with no failure, pending or neutral check;
the additional six came from ready-for-review advertising scope triggers.

The preceding PR #1879 release was already live while its official verifier
held the shared operation mutex. We waited for that verifier to finish; the
official shared `status` then returned **active:false**. No foreign lease or
process was cleared or stopped. Shared tracked files were clean at J; the
visible untracked platform files were preserved.

PR #1882 was normally merged after another fresh GitHub base/head/check read:
merge **4234a88a5e01ce179c5d5711ad80107ecd208a88**, parents J and frozen B,
tree **d696d29e828044c808778e95a20e48c3fb93f921**. Its tree is exactly B.
This preserves source A and the reviewed intent. Publication is authorized
but has not yet been submitted for this task.

Next: fresh inactive guard and clean tracked-file checks, fast-forward shared
`/app` to this merge, verify the exact B-equivalent tree, and run the governed
adapter with bytecode writes disabled and the reviewed public backend origin.
Retain sanitized build output, then own prepare/prepublish, submit one publish,
require the newer explicit successful deployment and official verify3/inactive.
No live receipt cancellation or invoice approval is authorized as a test.

A bounded read-only session diagnostic was attempted but stopped at import
because the default Python lacks `dotenv`; no database connection or data
operation occurred. Use an already installed backend interpreter when checking
the target later. The actual affected session remains unread/unverified.

**Production source branch changed: yes (authorized merge). Production runtime
changed by this task: no.** No owned release lease or live data mutation yet.


## Shared build and owned lease prepared

The official shared guard was checked again and was inactive; tracked files
were clean. Shared `/app` was fast-forwarded from J to normal merge
**4234a88a5e01ce179c5d5711ad80107ecd208a88**, after confirming its exact
B-equivalent tree. No reset or untracked-file cleanup occurred.

The actual shared Emergent adapter completed with **exit 0 in 66.65 seconds**.
Retained sanitized output:
`/tmp/supplier-receiving-1882-adapter-20261111T171333Z.log`.
Materialized source A and runtime identity matched the reviewed intent exactly;
tracked files remained clean. The merge's own Release Readiness run
[43065494129](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43065494129)
also succeeded, including both actual host adapter rehearsals.

Official `prepare` succeeded, exit **0**, at
**2026-11-11T17:17:16.351252+00:00**, actor
`codex-supplier-receiving-recovery-r2-20261111`. It binds source
`32919f806d17c9fd9d466b368abf807bd40ee925`, deployment merge
`4234a88a5e01ce179c5d5711ad80107ecd208a88`, and runtime
`rg5-fe87f7bb866db60c49025385479a29080e8f34fc02aef92e7bb395d50c3385b1`.
The exact prepare output is retained at
`/tmp/supplier-receiving-1882-prepare.json`.

**Our release lease is now active.** No publish has been submitted yet.
Next: official prepublish immediately before the already authorized single
publication, then observe new deployment success and run official verify3.
Do not repeat prepare or clear this lease; successful matching verify closes it.
The preceding visible publication `eb8949e` at Nov 11 19:51 Riyadh belongs
to PR #1879 and is only our baseline, not evidence for this supplier fix.


## Authorized publication submitted — 2026-11-11 20:22 Riyadh

Official prepublish completed with exit **0**, `ready_to_publish:true`,
protocol **5**, source A `32919f806d17c9fd9d466b368abf807bd40ee925`,
deployment merge `4234a88a5e01ce179c5d5711ad80107ecd208a88`, and exact
runtime `rg5-fe87f7bb866db60c49025385479a29080e8f34fc02aef92e7bb395d50c3385b1`.
The complete output is retained at
`/tmp/supplier-receiving-1882-prepublish.json`.

One authorized publication was submitted through Emergent Manage:
Republish, followed by the single final **Publish changes** confirmation.
The UI then showed **Publishing...** and
**Deployment Started Nov 11, 08:22 PM**, followed by **Building your website**.
No second publication was submitted. Our matching lease remains active.

**Baseline correction:** after a fresh reload, the preceding publication was
the **100th publish, `5e94faf`**, with the Nov 11 19:51 Riyadh completion
event. The earlier `eb8949e` attribution above came from stale UI and actually
refers to the older 99th publication. Neither is evidence for this supplier fix.
Accept only a newly successful deployment after `5e94faf`.

Production source changed by the authorized merge; deployment is now in
progress. Production runtime adoption remains **unverified** until the new
success event and official three-probe verifier complete. Next safe action:
observe this single attempt, then run the official verifier against
`https://mezansalla.com`, retain its result, and require inactive status.
Preserve the live affected receipt; no cancellation, approval, or invoice
mutation is part of verification.
