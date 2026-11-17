# Courier delivery history recovery — 2026-11-17

## Active revision 2: addressing CodeQL review findings before merge

The normal merge request for PR 1967 was rejected by GitHub with `All comments must be resolved`. No merge, production source update, release lease or publication occurred. The frozen first A/B pair is preserved without rewriting: A `41b510c36b4b57a4f1bff3a860f9e9ce84902906`, B `c59664ae63ad46fec1c6cb3c8a04b3dc71ce1617`. Both passed 15 applicable workflows; B also passed host 20.20.2 and 24.19.0 clean-clone/no-Git/package/runtime-route rehearsals. Green CodeQL workflow status did not resolve its three inline findings, so those badges were insufficient to merge.

CodeQL alerts 94 and 95 identify a genuine inherited polynomial expression in both upstream `removeBackslashes` utilities. It is reachable from the public `scan(..., {unescape:true})` API. Root reproduced four intended failures on the governed Node 22.23.2 runtime: direct helper and public scan for each major exceeded a 750ms process deadline on 60,000 unmatched opening brackets and were killed. The correction replaces the expression with an equivalent linear scanner, preserving bracket spans, backslash handling and JavaScript line-terminator semantics. Private fork versions advance to `mezan.2`; original upstream provenance is retained. Alert 96 points to an intentionally unsafe expression in a fixed-input test witness; that test now uses the explicit known capture expectation without constructing the unsafe expression.

Current task branch: `codex/courier-delivery-history-security-r2-20261117`, starting from source A41b510c so the required new source-A history contains no earlier intent-only B. A new reviewed A/B pair and superseding PR are required; the old frozen pair must not be squashed, rebased, or force-moved. The resulting new source SHA and PR will be recorded in the PR continuation evidence. Issue #1006 is full and remains untouched.

Revision 2 local verification is complete: independent review found no blocking issue after 212 complete helper/public-scan comparisons with the official releases. The tracked tests also bind 37,449 bounded upstream oracle inputs per major. The four new performance regressions fail on the original utility at the intended 750ms deadline and pass on the linear replacement; the focused new suite passed 10/10. Governed Node 22.23.2 / Yarn 1.22.22 frozen installation and installed-byte verification passed (3 copies, 8 consumer resolutions). The full combined security suite passed 210/210 with zero failures, skips, cancellations or todo. Strict production audit returned zero advisories at every severity over 409 dependencies; unchanged policy checker: exceptions NONE, blocking 0. Exact patched-file manifests were regenerated after both utility implementations were stable.

Next: save this immutable source checkpoint and require exact-source CI plus CodeQL review-thread inspection before freezing a new intent. Then repeat the governed release steps and live reconciliation measurement already authorized by the user. Production changed: **no**.

## Historical first remediation checkpoint (superseded before merge)

The user explicitly requested fixing the remaining dependency blocker. Work continues on the same task branch from source986083f; its previous accepted CI remains historical evidence, not acceptance of the changed dependency graph.

Two private, licensed, source-vendored backports preserve picomatch2.3.2 and4.0.7 as separate major APIs. Both tarballs were fetched from the official npm registry and SHA-512 verified. A bounded compiled-body guard rejects ambiguous/unsupported repeated expressions and only emits independently certified flat rewrites. Raw enclosing quantifiers cannot bypass the guard. Token and quote handling ensure final compiled output is the output checked. The strict audit policy is unchanged; added integrity and security checks cover every installed copy and consumer resolution.

Independent development reviews found and resolved quoted-token accounting, Unicode surrogate union, greedy-outer capture priority, and raw-wrapper option/adjacent-extglob distinctions. Original attacks were reproduced in isolated killed child processes:8 intended RED failures at750ms across both upstream majors. Positive baseline cases passed. Development public behavior checks passed 134 cases; final raw-wrapper/capture checks passed 34. The governed Node 22.23.2 / Yarn 1.22.22 frozen offline installation completed successfully. The installed integrity verifier passed: 3 physical copies and 8 consumer resolutions, with no upstream copy accepted. Combined strict-audit, braces and picomatch regression tests passed 202/202 with zero skipped, cancelled or todo tests. One initial Rollup test used an input outside its default resolution root; explicit rooted and unrooted consumer cases now pass without changing runtime behavior. The strict production audit returned a complete 409-dependency summary with zero advisories at every severity; its unchanged policy checker reported exceptions NONE and blocking advisories 0. These local checks are not yet immutable-source CI or release acceptance.

Changed surfaces for this continuation: `frontend/vendor/picomatch-v2/`, `frontend/vendor/picomatch-v4/`, their explicit consumer-specific resolutions in `frontend/package.json` and `frontend/yarn.lock`, `frontend/scripts/verify-picomatch-backports.cjs`, `frontend/scripts/picomatch-security.test.cjs`, the existing Security Gate wiring, and this status. No courier/accounting authority or financial data changes are added by the dependency remediation.

Verified commands (exit 0): `python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend install --offline --frozen-lockfile --force --non-interactive`; `node frontend/scripts/verify-picomatch-backports.cjs`; `node --test --test-reporter=spec scripts/test_frontend_audit.cjs frontend/scripts/braces-security.test.cjs frontend/scripts/picomatch-security.test.cjs`; and `node scripts/check_frontend_audit.cjs <complete-audit-jsonl> 0`, with all Node commands run through the governed toolchain. The shared helper SHA-256 is `2d6f345b66e1575df07ecec53dcdccc4dc4e6bd4cfaf8f105d6094b9de131c87`. Complete patched runtime hashes and original npm provenance are tracked in each vendor directory.

This checkpoint extends source `986083f9344ea0b980263d3228f89c984e675cfd`; its resulting immutable commit is recorded in PR 1967. Fresh remote inspection confirmed production base `8df479ef24b4f8847c26ba18d48e3248f7b673cf` remains current.

Next: require the full relevant CI and strict Security Gate on this immutable checkpoint. Then create a new governed A/B release under AGENTS.md and resume the already authorized merge/publish only if every required gate passes. No extra user permission is needed for that existing authorization. Production remains unchanged; no lease or deployment has started.

## Historical checkpoint: source986083f before security remediation


- Task: bounded visible courier evidence recovery and explicit matching of a saved pending order.
- Repository: `AMASI-SA/AMASI-SA`.
- Source branch: `codex/courier-delivery-history-recovery-20261117`.
- Source candidate A: `986083f9344ea0b980263d3228f89c984e675cfd`.
- Source tree: `ffc20f4f2712814d3ae8ea03e520b05f4dd73d9d`.
- Verified production base: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1967 .
- Final handoff branch: `codex/courier-delivery-history-evidence-20261117`.
- This evidence-only branch updates this document without advancing the reviewed source candidate. The PR body records its resulting immutable commit SHA.
- Production changed: **no**. No merge, release lease, clean-clone deployment rehearsal, or publication was performed. No live recovery or financial update was executed by this task.

## Confirmed failure modes and completed behavior

The existing native verifier accepts a synthetic reproduction of a single original delivered shipment with no shipment-level delivery timestamp and complete same-carrier dispatch/delivery history. No date parser or delivery-proof acceptance change was justified by this reproduction. The corresponding live production source record has not been verified or repaired by this candidate.

Four independently reproduced regressions explain why usable history can remain unavailable to the saved operational report:

1. An expired invalid prefix repeatedly consumes the four-order selection allowance, starving never-attempted orders.
2. Due orders are selected in insertion order instead of oldest retry order.
3. One native transport failure abandons the remaining bounded allowance.
4. A hanging bank adapter consumes the entire visible evidence budget before the courier adapter begins.

The implementation atomically prefers never-attempted and oldest-due courier gaps; isolates provider transport failures per order; and reserves time for each remaining source within the unchanged serial twenty-five-second budget. Adapters are cancelled and joined before advancing. The four-order cap and per-order fifteen-minute cooldown remain.

Native recovery now returns separate verified, saved, unchanged, blocked, unavailable, and source-conflict outcomes. Saving requires the existing full compare-and-set match on the source snapshot, current shipment and watermark. A successful aggregate refresh alone is not reported as successful native recovery. Timeout outcomes omit unknown counts, and arbitrary provider exception text is not returned.

The frontend adds explicit `مطابقة مع سلة` to eligible courier pending rows. The selected order must intersect the current owner's saved courier issues and match the current session scope. It performs one targeted native acquisition followed by the existing shared aggregate and one guarded report read. Close, hide, offline, navigation, authorization changes, and unmount cancel the request; there is no replay or background retry. Explanatory results appear below the pending-order table and remain visible if the corrected row disappears. Saved evidence is distinguished from unchanged evidence and from delivery proof. Existing manual delivery confirmation remains a separate action.

Independent review found and fixed an export overlap issue: revoking an earlier export now clears its busy/error presentation state even if the subsequent match cancels or fails without replacing the snapshot. Four regression cases cover page/full export crossed with match cancellation/failure.

No delivery date is fabricated. Owner, session, proof, carrier, waybill, source-version, cutover, authorization, cancellation and financial guards remain enforced.

## Changed files at source A

Backend runtime:

- `backend/salla_integration/courier_delivery_sync.py`
- `backend/operational_visible_evidence.py`
- `backend/operational_demand_refresh_routes.py`

Backend verification:

- `backend/tests/test_operational_balance_courier_acquisition_progress.py`
- `backend/tests/test_operational_balance_courier_targeted_acquisition.py`
- `backend/tests/test_operational_balance_demand_refresh_routes.py`
- `backend/tests/test_operational_balance_visible_evidence.py`

Frontend runtime and paired tests under `frontend/src/pages/operational/`:

- `OperationalFinancialPosition.jsx` / `OperationalFinancialPosition.test.jsx`
- `PendingOrdersDialog.jsx` / `PendingOrdersDialog.test.jsx`
- `api.js` / `financialPositionApi.test.js`
- `visibleOperationalRead.js` / `visibleOperationalRead.test.js`

The source checkpoint also includes the earlier revision of this status document.

## Exact-source verification accepted

Operational Balance Web run: https://github.com/AMASI-SA/AMASI-SA/actions/runs/44141898374 . All four jobs succeeded on source A.

| Check | Accepted result |
| --- | --- |
| Backend with genuine MongoDB 8.0.12 replica set | 5,151 passed; zero skipped, failures or errors |
| Additional shipping summary/calculator checks | 16 passed and 18 subtests passed |
| New progress regressions | 4/4, including both genuine-Mongo sorted-claim cases |
| New targeted acquisition regressions | 21/21 |
| Existing native courier acquisition | 79/79 |
| Existing waybill acquisition | 14/14 |
| Operational frontend contracts | 1,000 tests / 62 suites; zero failures, pending or todo |
| Browser/mobile/visibility acceptance | 14 operational scenarios and 8 simple-balances scenarios passed |
| Canonical worker exclusion | Passed |

Independent review checked the source and tree in the downloaded backend evidence artifact, not just the workflow status. Backend artifact 14353789680: https://github.com/AMASI-SA/AMASI-SA/actions/runs/44141898374/artifacts/14353789680 . ZIP SHA-256: `3088ea347be104211bb07e72427b3716546f38ed9f7e18f0e3cd2d66af4e300f`.

The earlier `7116da02320807bb6f7dc4c5dd5a0028811ce43e` backend run was cancelled and is not counted as acceptance. Its frontend result preceded the export regression fix. Final results above are for A only.

Root verified thirteen PR workflows: twelve succeeded; Security Gate is the sole failure described below. Within that failed workflow, the general frontend suite passed 4,246 tests / 417 suites; backend security contracts also passed. The later frontend CSP/build steps were skipped after the audit failed and must not be claimed as passed.

Initial RED checkpoint: `3e42238ccfcd42eddf5890f09357ac68d04f3431`. Root independently reproduced all four intended failures against unchanged production source using:

```bash
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/mezan-courier-test-venv/bin/python -m pytest --noconftest \
  -p no:cacheprovider -q --tb=line \
  backend/tests/test_operational_balance_courier_acquisition_progress.py
```

RED result: four failures at intended assertions, exit 1. Focused implementation checks later passed 66 tests, exit 0, and targeted acquisition passed 21 tests, exit 0. The final sorting regressions use genuine Mongo in CI because mongomock 4.3.0 returns the sorted document but updates the wrong document when sorted find-and-modify excludes `_id` from projection. Production projection was not changed to accommodate this emulator defect. The local official Mongo binary could not start because this runtime lacks required sysfs CPU metadata; no binary or system-file workaround was attempted. CI resolved this verification gap without skips.

## Governed build preparation

Verified official toolchain: Node 22.23.2 / Yarn 1.22.22. Frozen dependency installation exited 0 without tracked changes. The governed source A build command was:

```bash
MEZAN_RELEASE_BOOTSTRAP_INTENT=1 REACT_APP_BACKEND_URL=https://mezansalla.com \
  python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend build:release
```

Both clean builds matched; exit 0. Artifact tree SHA-256: `6d5e1fd2c721c08e2ccce25f28eb35f36ee6dc942f7d8615479649cecd0c3954`. Reproducibility proof SHA-256: `c708fe013a19149404417879734779a2c4bbf9d59b0c85dda350924ec93b5e89`.

```bash
python scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py \
  --expected-git-sha 986083f9344ea0b980263d3228f89c984e675cfd
```

Verification exited 0. A local intent freeze also exited 0 after removing two generated, untracked Python bytecode files and disabling bytecode generation. It was written outside the worktree; the tracked intent was not changed. Frozen candidate intent SHA-256: `013cad17a93891f3fcf3c511615f97d47d3361c8b52704754675cd92af5af6fc` (1,338,136 bytes). Candidate runtime ID: `rg5-5bd96a691e5309473999fbe6c1abe4525b1c9d651ab8c6ca50c1692da9a253a9`.

This is preparation evidence only. No intent-only B commit or clean-clone adapter rehearsal was completed, and no runtime identity was materialized in Production. Any source or dependency remediation requires a new A and newly frozen intent. Recreate local ignored outputs from the reviewed source; never rely on their persistence.

## Blocking Security Gate finding

- Run: https://github.com/AMASI-SA/AMASI-SA/actions/runs/44141898393 .
- Failing job: https://github.com/AMASI-SA/AMASI-SA/actions/runs/44141898393/job/134246616705 .
- Failed step: production frontend dependency audit, 2026-11-17T17:56:11Z.
- Advisory: https://github.com/advisories/GHSA-fc5q-39xc-vjpf (`CVE-2026-112366`), high-severity repeated-extglob regular-expression denial of service in `picomatch`.
- The official advisory lists versions through 4.0.7 as affected and no patched version. Official npm metadata and the latest upstream release both still identify 4.0.7 as latest at review time. The referenced upstream PR is not a complete released fix.
- Installed vulnerable versions are 2.3.2 and 4.0.7, reached through five paths: tailwindcss → micromatch; tailwindcss → chokidar → anymatch; vite directly; vite → tinyglobby; and tailwindcss → sucrase → tinyglobby.

This dependency graph predates the courier change. Production base Security Gate run https://github.com/AMASI-SA/AMASI-SA/actions/runs/44001199684 reported high=0, exceptions=NONE, blocking=0 at 2026-11-17T02:14:48Z. The advisory was reviewed/updated later on November 17; it was originally published earlier. Byte comparisons show these files are unchanged between the production base and A: `frontend/package.json`, `frontend/yarn.lock`, `scripts/check_frontend_audit.cjs`, `.github/workflows/security-gate.yml`, and `frontend/scripts/verify-operational-dependency-compat.cjs`.

The governed runtime starts Vite preview, so build-only/non-applicable status has not been established. No exception, gate weakening, dependency reclassification, version downgrade, fake version, or unreviewed upstream patch was applied. The source remains a draft and must not be merged or published while this gate is unresolved.

## Production and continuity state

Read-only inspection of the signed-in Emergent project found `/app` at production base 8df479ef, no tracked or staged changes, and 120 unrelated untracked files, all preserved. Release Guard status reported `active: false`. No checkout, source edit, dependency installation, rehearsal, lease or deployment was performed there. Temporary diagnostic terminal sessions were closed; the user's unrelated unsaved editor was preserved.

The live Mezan pending-order view still showed the selected case. A read-only exact-case lookup in the Emergent workspace database did not locate it; that environment was not established as the production data store, so this does not prove the case is absent from production. No native recovery, reduction in pending count/value, or live financial/shipping reconciliation is claimed.

Issue #1006 has reached its comment limit. Do not overwrite its history or retry appending. This status document and the PR description are the continuation record. No customer records, credentials or provider payloads are included here.

## Exact next safe action

1. Re-read this evidence checkpoint and PR 1967; fetch the source branch and verify A and the current production base before edits.
2. Resolve the existing dependency advisory with a supported fixed release or an independently reviewed removal/replacement of all affected paths; retain the strict audit. A partial upstream patch is insufficient.
3. If source changes, create a new source A, require the relevant CI and security gates, rebuild, and freeze a new intent-only B under AGENTS.md protocol v5. Do not squash or rebase a frozen A/B pair.
4. Recheck Release Guard, complete the clean-clone adapter rehearsal, then resume the previously authorized normal merge and governed publish only after all gates pass. Require a newer explicit Deployment Succeeded, three matching production identity probes, and inactive guard afterward. User authorization is already present; do not ask again for the same action.
5. On the verified live release, explicitly match the user's selected pending case and inspect the native receipt plus new report revision. Measure before/after pending orders and amounts over the same period and scope. If proof remains blocked, use its precise reason and inspect the authoritative production source; do not substitute order creation/update time or assert delivery manually to clear the discrepancy.

Do not report completion of the user's live reconciliation goal until that final measurement is verified.
