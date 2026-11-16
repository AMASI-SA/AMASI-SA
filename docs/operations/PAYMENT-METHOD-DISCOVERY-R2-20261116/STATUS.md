# Payment-method discovery: isolate conflicting methods

## Task and checkpoint

- Task: `PAYMENT-METHOD-DISCOVERY-R2-20261116`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/payment-method-discovery-r2-20261116`.
- Exact verified production/source base J: `71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`.
- State: smallest confirmed live-acceptance fix implemented, regression-tested and independently reviewed; fresh source/intent release pending.
- Production changed by this R2 source: **no**.
- The preceding feature PR1962 is merged and published. Its new revision `0680799` reported `Deployment successful` in Emergent, operation `ecbb3454-9e59-58b0-af49-6f8e34cc2267`. One Publish action was submitted. Its first official verification returned HTTP520 during startup; the subsequent official verification is still in progress against its own lease. Do not clear or replace that lease, or assume it is inactive.
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

## Next safe actions

1. Preserve this tested checkpoint on the task branch and record its exact source SHA in the PR. The prior frozen A/B branch must remain unchanged.
2. Finish the prior release's official verification and require final `active:false` before any shared `/app` change or release rehearsal.
3. Build the fresh source A with the governed toolchain and exact public client origin; freeze a new external reviewed intent and create intent-only B. Never reuse the previous intent/runtime identity.
4. Require exact B CI, both clean-clone adapter rehearsals, current production base and tree-equivalent normal merge. Follow AGENTS protocolv5 for shared adapter, prepare, prepublish and the separately authorized publication.
5. Require explicit newer deployment success, all three official production probes and inactive final guard. Revisit the exact-method deep link and verify blank editable terms without saving any real commission.
6. Record final release identities, CI, deployment and live acceptance in a separate documentation handoff so frozen runtime commits are not amended.

## Preserved scope

No real commission, payment, ledger entry, ad budget, campaign, provider policy or source-mode selection was changed. The existing compact ad card and refresh cadence remain unchanged. The actual merchant terms still require owner entry; this task must never invent rates or convert missing values to zero.
