# Payment-method discovery: isolate conflicting methods

## Task and checkpoint

- Task: `PAYMENT-METHOD-DISCOVERY-R2-20261116`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/payment-method-discovery-r2-20261116`.
- Exact verified production/source base J: `71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`.
- State: shared adapter passed; a task-owned R2 lease is prepared. Exact-M10of11checks passed at this checkpoint; only CodeQL Python remained running. Prepublish and R2 publication have not been submitted.
- Production Git branch changed by R2: **yes**, through PR1963. Live R2 publication: **no**, not yet submitted.
- The preceding feature PR1962 is merged and published as `0680799`, operation `ecbb3454-9e59-58b0-af49-6f8e34cc2267`; Emergent reported `Deployment successful`. Its first official verification hit startup HTTP520; the second finished normally with **exit0, verified:true and all3 probes**, matching source A1/deployment M1/runtime. Final official status is **active:false**. No interrupt, abort, second Publish or lease replacement occurred. Runtime verification passed; the functional defect below still requires R2.
- Prior frozen source A: `03c91e903c992a2e397fccab2124e07fe002e0e6`; intent B: `03605c83e67c26b8f92477624f92d662fb97616e`; merge M/current base J: `71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`.
- Prior runtime: `rg5-2e67a08592737d05609cbedd7b422876345be2ac13df2d97ea6f0e44066a24cb`.
- Prior documentation-only checkpoint: `6d654a622fc987fba1ffd038e94c72a939bc7481` on `codex/payment-method-discovery-handoff-20261116`. Never merge or deploy that branch.
- Issue1006 is at its 2,500-comment cap. The task PR and STATUS are the continuation record.

## Concrete defect and repair

Live acceptance confirmed the new dashboard notice and exact-method navigation, but a conflict in one existing method's aliases set the whole directory's `complete` flag to false. Consequently every method became immutable, including an unrelated new payment method, and the UI incorrectly described a successful read as a read failure.

`backend/payment_method_setup.py` now leaves `complete` as the indicator of directory/settings read integrity. It no longer changes that global flag when one row's fee lookup is ambiguous. Conflicting rows retain unspecified terms and `editable:false`. The unchanged save boundary still rejects an ambiguous or inherited target with HTTP409. Unrelated rows remain available under the existing authorization and concurrency checks.

True incomplete reads, timeouts, overflow, invalid/unavailable/foreign settings and duplicate owner documents still prevent all editing. No matching precedence, fee calculation, owner authorization, revision/CAS protection, discovery query, financial value or frontend implementation changed.

## Changed files

- `backend/payment_method_setup.py`: one removed per-row mutation and explanatory docstring.
- `backend/tests/test_payment_method_setup.py`: updated conflict semantics and mixed-conflict/independent-method regression in both settings orders.
- `frontend/src/pages/operational/ObservedPaymentMethods.test.jsx`: deep-link and authoritative-save regression with an immutable conflicting row beside an independently editable row.
- This STATUS document.

## Verification and independent review

The three targeted backend regression cases first failed on the previous source specifically because `complete` was false. After the correction:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_payment_method_setup.py
```

Result: **51 passed, zero failures, exit0, 1.16 seconds**. This includes existing timeout/overflow/unavailable/owner/CAS protections. New assertions prove read-only discovery, explicit independent save and authoritative readback, byte-for-byte BSON preservation of all other method/settings content, and continued409 rejection without writes for conflicting targets.

```bash
CI=true PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath \
  src/pages/operational/ObservedPaymentMethods.test.jsx
```

Result: **13 tests, one suite, exit0**, using governed Node22.23.2/Yarn1.22.22. The new test verifies the exact-method link opens three blank fields, submits explicit synthetic terms and expected revision, waits for authoritative confirmation and leaves the other row locked. Scoped diff checks passed.

The frontend reviewer independently reviewed the backend source/test diff and approved it with no material finding. Review confirmed row-local conflict protection and unchanged genuine read-failure/authorization/save protections. Root reviewed both diffs. No real merchant fees or customer data were used in tests or written by this task.

## Frozen reviewed R2 release
- Source A: `39c891d7b1da06d01e75b71bb42310eb8d29f066`.
- Source tree: `03491ab6bd54b4a8a9e0db9a6fb0f9073eb19db4`.
- Intent-only B: `ec6b3a1b808ca7bb4af081f7c63ea28796d89617`.
- B tree: `70842fa4b3db6c9cea6a4b4d743a804f078f7e52`.
- PR: https://github.com/AMASI-SA/AMASI-SA/pull/1963 (merged normally).
- Production merge M: `438b461fc660e043726c8dd5ab282d06abd3c17f`; tree equals B, parents are J and B; official Git transition validator accepted M.
- Previous documentation checkpoint: `eeeb99d4262c7412045e184ac2206669301dd481`.
- Runtime: `rg5-ddac746f37c8ee913405c42ced5804aa9e68ff17b56ba60a60ff2a824a7ffe8e`.
- Frontend artifact: `a31d130116eaa72d7ba3a0ec0f36fda19c29db272d207642d520482df16e46ff`.
- Reproducibility proof: `1a7f37962dc5acfe31e346f90d3c75e259b49361fda98c29d890ce92b8a3167f`.
- Intent SHA256: `a421b93d18b796adffc567f8c691721fb53d1c0844603ea54ff2c154d2025480`.
- Local source-A two matching builds, frontend verifier, external intent freeze/candidate validator and official B Git transition validation all exited0. Fetched B intent exactly matches the validated candidate; A→B changes only the intent and tracked worktree is clean.
- Exact-A Readiness `43945209388` succeeded and produced candidate artifact `14263972896`. A operational frontend `133572941074` passed920tests/56suites. These do not substitute for the fresh exact-B gates.
- This is a documentation-only handoff on `codex/payment-method-discovery-r2-handoff-20261116`. Never merge or deploy it. Frozen runtime branch remains at B.

## R1 verification receipt
The successful prior receipt is `/tmp/mezan-payment-method-release-1962/verify2.json`; exit record `verify2-exit.json`; final status `final-status.json`. Three probes observed boot identities `2026-11-16T19:51:55.134941+00:00` and `2026-11-16T19:51:49.045223+00:00`, with the exact reviewed frontend artifact. The verifier checked canonical/cache-busted metadata, full public files, shell and service-worker contracts, then closed its matching lease. No R2 lease has been prepared.

## Next safe actions
1. Confirm completion of the last exact-M CodeQL Python check. Readiness and Security, both actual clean-clone rehearsals and CodeQL JavaScript already passed.
2. Preserve this task's prepared lease and shared M source. Do not prepare another lease or repeat the shared build.
3. Immediately before the authorized Publish, run official prepublish against this lease and require exact reviewed A/M/runtime readiness, then click Publish once.
4. Require a newer explicit deployment success, all3 official production probes and final inactive guard. Revisit the exact-method link and verify editable blank commission terms without saving real values.
5. Update this separate handoff and PR with final exact merge, release receipts and live acceptance. Do not amend the frozen runtime branch.

## Preserved scope

No real commission, payment, ledger entry, ad budget, campaign, provider policy or source-mode selection was changed. The existing compact ad card and refresh cadence remain unchanged. The actual merchant terms still require owner entry; this task must never invent rates or convert missing values to zero.

## Exact-B and shared-source milestone
All35 returned exact-B check records completed: **24success, 8skipped, 3cancelled**. The3 cancellations belonged to superseded Snapchat Direct run43947081870; its replacement43947133001 on exact B succeeded at scope and intentionally skipped its two out-of-scope jobs. No failed or pending check remained. Fresh PR head/base were B/J, mergeable clean, with actual production ref still J immediately before normal merge.

Readiness43947081805 succeeded with both actual clean-clone rehearsals: Host20.20.2 job133580156661 and Host24.19.0 job133580156496. Four Platform Spend43947081853 and Operational Balance Web43947081942 succeeded. Exact-M checks run separately after merge; B results do not replace them.

Shared update command obtained fresh official active:false, checked expected J HEAD and empty tracked/staged diffs, fetched the unchanged production M and checked its reviewed B tree, then fast-forwarded normally. Shared observed HEAD is M, tree70842fa4b3db6c9cea6a4b4d743a804f078f7e52, tracked_clean:true, guard_was_inactive:true. Unrelated untracked files were preserved.

The configured `cd /app/frontend; REACT_APP_BACKEND_URL=https://mezansalla.com yarn build` was started after another official inactive-guard check and expected-M check. Receipt directory: `/tmp/mezan-payment-method-release-1963/`. No prepare/prepublish/Publish has been issued for R2.

## Shared adapter and owned R2 lease
The configured adapter completed **exit0**; generated backend identity exactly equals the reviewed intent. Observed source A, runtime and frontend artifact match the frozen R2 values above. Receipt: `/tmp/mezan-payment-method-release-1963/adapter.log`; exit record `adapter-exit.json`.

Official prepare created the task's active v5 lease at **2026-11-16T20:23:52.530472+00:00**, actor `payment-method-discovery-r2-20261116`, source A39c891d7b1da06d01e75b71bb42310eb8d29f066, deployment M438b461fc660e043726c8dd5ab282d06abd3c17f, runtime rg5-ddac746f37c8ee913405c42ced5804aa9e68ff17b56ba60a60ff2a824a7ffe8e. Fresh official status confirms that exact active lease. Receipt: `prepare.json`. No second prepare, abort, prepublish or R2 Publish occurred.

At this checkpoint exact-M Readiness43948563014 and Security43948562868 passed all9jobs; CodeQL43948563334 JavaScript passed and Python133584631274 was still running. Do not claim all11passed until the last result is observed. This active-lease section supersedes the earlier build-in-progress chronology.
