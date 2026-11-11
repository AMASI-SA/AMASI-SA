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
