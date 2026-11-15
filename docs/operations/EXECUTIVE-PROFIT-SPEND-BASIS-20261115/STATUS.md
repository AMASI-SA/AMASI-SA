# Current operational checkpoint — 2026-11-15 14:48:14 UTC

The profit fix is merged as `a2a8218f858547f4d9d094d29998a93b09bf59ae`; its complete source, B, and actual-M gates passed. **The profit fix is not published or live-verified yet.** The pinned original operator remains staged and unexecuted. This task has not changed shared `/app`, created a lease, or published.

The preceding Salla owner published version `908dee3`. Root directly observed the official guard return `active: false` at about 14:24 UTC after that owner's verification lock released. Functional Salla acceptance remains open because its installed capture wrapper rejected the strict-processing keyword before processing queued events.

The owner opened draft PR #1940 with source `a6c5bc5763fdca144b44473285d37130eb8213e9`, tree `e4cd5f6f07aa8e3d94ce729231b21db41faf619e`, sole parent our M. Root and independent review verified exactly five changed Salla/test/workflow/status paths and all six profit-fix blobs unchanged. The explicit boolean is forwarded with default False; strict capture failures propagate outside the optional reconciliation catch. Tests cover real installation order and fresh worker completion. All 12 exact-source workflows passed on first attempts by 14:32 UTC. Independent review of actual job logs and downloaded JUnit confirms Salla 355 passed, Background 293 passed plus 88 preservation cases, all zero skips/failures/errors. The strengthened real-Mongo fresh-child event-completion case executed and passed. Root fetched and verified intent-only B `ac1443d46d6c172983dfe74479c6f96c40b0c4ec`, sole parent accepted A, exact tree `d5bb8d88f7f04610dd6796648edebca24d025c1c`. The only A-to-B change is the intent. Its SHA256 is `812c3b0bdb27215b6647d40055616ded848d118fc0a7879b289f70a5c02a1c6d`; runtime is `rg5-f33cd3537f17e7a25804236a8b4ad0680c73618af6d3cddabc75fe8486f8d93a`. The governed frontend artifact tree remains exactly our reviewed `8f6bb73cec2d628eadb120994d2f4ab1fab730eb0637bad8f7c8a050770ea943`. All 12 distinct exact-B workflows passed, with 14 successful first-attempt runs including the two ready-event Snapchat checks. Both actual Host20/24 clean-clone rehearsals passed. The owner records independent archive/package review PASS, proof SHA256 `9d22c541d4697d7e23c0e8a21f09af2da69cf22010f7e4ec80dc18089136030e`.

PR #1940 merged normally at 14:42:09 UTC as `d7facb0ff4e2e30edbabf012b3865155d5902a85`. Root verified actual Git parents are our M then successor B, and its tree exactly equals B. Production branch points to that M. Root independently verified all exact actual-M push gates PASS at 14:48:14 UTC: Security 43725789566, CodeQL 43725789598, Readiness 43725789610. All 11 actual checks succeeded, including both adapter rehearsals and the executed push-only manual handoff; no pending, failure, or skip remains. The old staged operator is superseded and must not execute. The owner handles the combined original rehearsal and publication.

The Salla owner controls the successor release. Follow that reviewed descendant through its own A/B/M and original-workspace protocol instead of publishing the obsolete M. Do not run this task's staged M operator blindly. Quarantined rows do not automatically resume; bounded data recovery remains outside this profit task. Issue #1006 is full; this status, PR #1936 and `release-evidence.json` on the evidence branch are the recoverable continuation. Never merge the evidence branch into the frozen source release.

Next: verify exact successor gates and publication evidence, then live executive net-profit visibility and canonical spend basis. Production changed by this task: no.

Historical source checkpoint follows.

---

# Executive profit spending basis — 2026-11-15

## Scope and source

- Repository: AMASI-SA/AMASI-SA.
- Release task branch: `codex/executive-profit-release-20261115`.
- Current reviewed production branch base: `331b7917b3c01d064ee2a17affb4714f3a296331` (PR #1935).
- Original implementation base: `9afb38cf0dc0d8e8cd887f2c2cdb4a6be5a7ca68`; preserved local implementation: `1ccf67d19460e5a207b9751dc8c9c71ff38f1169`.
- User request: inspect and fix missing net profit in the Advanced Dashboard executive summary.
- The user explicitly approved uploading this fix to AMASI-SA/AMASI-SA, merging it, publishing to mezansalla.com, and checking the live result on 2026-11-15.
- Production changed: **no**. No merge, release lease, publication, provider synchronization, advertising or accounting mutation has been performed.

## Confirmed mechanisms

The live dashboard's pending-financial-basis message was reproduced. A same-period saved-data comparison established that the executive summary uses direct TikTok facts while the platform-spend display replaces them with the old Make ledger amount. Other provider amounts and the Snapchat native/SAR fee basis matched in the inspected case. A manual executive refresh could not resolve two different source authorities.

The frontend has a second, independently reproduced gap: its rendering gate detects an available-to-available spending or account-basis change, but its canonical refresh admission handles only unavailable-to-available changes. No live business values or customer payloads are retained in this record or the synthetic tests.

## Backend checkpoint

- Connected API TikTok accounts now establish the whole requested range's authority, with the same owner/account selection and shared account FX calculation as the executive reader.
- That direct path never reads the old Make ledger or update timestamps. The historical bridge remains available only without connected native account authority.
- Observed zero remains zero. Missing or unusable account-day facts and a truncated bounded read remain incomplete, with known amounts reported separately.
- Range totals are rounded once, after the canonical effective row sum. Direct daily facts never become a Make hourly marker; native hourly facts are limited to the same account scope.
- Meta source behavior, financial formulas, account settings, synchronization schedules, and existing product-cost completeness rules are unchanged.
- The regression file is registered in the existing four-platform PR workflow.

## Verified implementation evidence

The following results were directly produced on the preserved implementation. The incoming PR #1935 has no changed path in common with this fix. All five code/test/workflow blobs are carried onto the new base byte-for-byte; only this handoff document changes. The current-base source must pass fresh affected tests and its exact-head release gates before any intent is frozen or production is changed.

Backend RED: 11 new cases failed for the intended mismatches; the historical Make-only control passed.

Backend GREEN, root-run: **152 passed, zero skipped, exit 0**. Two existing Pydantic deprecation warnings remain in unrelated recurring-obligation models.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend \
python -B -m pytest --noconftest -p no:cacheprovider --asyncio-mode=auto -q --tb=short \
  backend/tests/test_dashboard_tiktok_native_spend_basis.py \
  backend/tests/test_dashboard_four_platform_spend_v1.py \
  backend/tests/test_google_dashboard_spend.py \
  backend/tests/test_snapchat_reporting_v2_account_display.py \
  backend/tests/test_snapchat_reporting_v2_operational_dashboard.py \
  backend/tests/test_dashboard_snapchat_spend_issue_727.py \
  backend/tests/test_dashboard_v2_ad_account_costs.py
```

Independent read-only review passed 27 focused backend tests and a display-module-first import check. No material backend blocker was found. `git diff --check` passed.

## Frontend correction and fresh verification

The frontend loader now requests one endpoint-scoped canonical recalculation when an available spending snapshot differs from the executive financial basis. The existing interval, filters, stale-request fences, and backend ownership of profit and fees are preserved. An already explicitly refreshed request does not get a redundant mismatch-only rebuild.

An actual-source loader reproduction failed before the change (one read retained the old unmatched financial response), then passed after it (two reads and the new server-owned financial response). Independent review executed ten actual-source loader scenarios successfully, including persistent mismatch, account/provider shifts, explicit refresh, wrong-period/unavailable spending, filter preservation, and a stale second response.

Full mounted Jest verification under governed Node 22.23.2 and Yarn 1.22.22: **252 passed across 9 suites, zero skipped, exit 0**. The new mounted cases prove that a newer complete or provisional server response restores rendered net profit, payment fees, CPA, and ROAS without a provider sync or unrelated card refresh. Existing layout, latest-orders, spend-card, snapshot, and scheduling regressions pass.

```sh
CI=true python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath \
  src/pages/AdvancedDashboard.cardRefresh.test.jsx \
  src/pages/AdvancedDashboard.latestOrders.test.jsx \
  src/services/dashboardAdsSpend.test.js \
  src/lib/dashboardPlatformSpendMerge.test.js \
  src/components/DashboardAdsSpendCard.test.jsx \
  src/components/DashboardAdsSpendCard.manualRefresh.test.jsx \
  src/pages/AdvancedDashboard.profitLayout.test.jsx \
  src/services/dashboardCardSnapshot.test.js \
  src/hooks/useDashboardCardSnapshot.test.jsx
```

## Build identity and handoff

The supported local source-candidate check is the explicit bootstrap path below, after committing the source locally. It creates ignored A/B build and proof outputs, without consuming the old reviewed intent, freezing a new intent, creating a lease, or publishing. The resulting full source SHA, build result, and patch SHA are recorded in the separately packaged `verification.json` to avoid a self-referential commit identity.

```sh
MEZAN_RELEASE_BOOTSTRAP_INTENT=1 REACT_APP_BACKEND_URL=https://mezansalla.com \
  python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend build:release
python -B scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py --expected-git-sha "<full-local-source-sha>"
```

Local backend checkpoint: `1ff14da02bb5110396da95465e64449b61cd8d8d`. The initial branch push was blocked by automatic approval review until explicit destination authorization. The user subsequently approved that exact action, merge, and publication. A normal authorized retry found no CLI GitHub write credentials, so the connected GitHub API is the supported upload path. No credential extraction or approval bypass is used.

Production advanced to PR #1935 while the user approved the implementation. Preserve that update by deriving a new single-parent source from the current base, inheriting its reviewed intent unchanged. Do not reuse the prior source's build identity. Source and intent identities, exact-head CI, merge and deployment evidence must be recorded in the task PR and a separate evidence checkpoint without modifying the immutable source/intent pair. Issue #1006 has reached its comment limit, so task status, PR and evidence branch are the recoverable handoff.

The shared Emergent `/app` lease was directly observed inactive at the start of this continuation, with the preceding published source still checked out. PR #1935 publication may proceed independently, so recheck the official lease and actual HEAD before touching `/app`. Never close another owner's lease. Next safe action: upload the source checkpoint, run fresh affected tests/build and exact-source CI, freeze the independently reviewed candidate into an intent-only commit, accept its rehearsals, then perform the separately authorized guarded publication after the preceding release is complete.

## Source-gate fixture correction

The first remote source, `33f9683125df1640cc72c5d4276ac85c370cfba7`, passed fresh local 152 backend and 252 frontend checks plus governed A/B build verification. Its exact-source Snapchat foundation CI run `43719156396`, job `132827158905`, then reported 559 passed and two failures in the parameterized `test_four_platform_route_forwards_partial_and_counts_snapchat_once`.

Both failures were the new native TikTok reader reaching a bare `object()` database sentinel. That existing route-level test explicitly stubs every saved-data reader; its fixture needed the newly added reader stub. The correction adds only the missing `_native_tiktok_spend` mock in `backend/tests/test_snapchat_reporting_v2_midnight_zero_dashboard.py`. All financial assertions and product behavior remain unchanged. The intended failing pair subsequently passed (2 passed, 16 deselected). This is an introduced fixture integration gap, not an unrelated failure or an environment waiver. The successor source requires its own complete CI and candidate evidence; the previous candidate is not reused.

The official candidate loader also correctly rejected locally generated Python bytecode caches. Remove only ignored generated caches before candidate inspection and use `PYTHONDONTWRITEBYTECODE=1` for verification children. Do not weaken the package-source check. Failed attempt evidence remains available in the PR and CI run.

The first source's Stock gate (`43719156300`, job `132827158080`) separately reported 4512 passed, 7 subtests passed and one failure in `test_actual_source_conversion_stops_before_draining_rows_and_joins_on_disconnect[advertising]`: the worker processed all 20000 fixture rows before the test observed disconnect cancellation. Its test and operational conversion/cancellation modules are unchanged from J. The observed scheduling path is consistent with timing sensitivity, but this is not a waiver or proof of a harmless failure. Preserve the failed attempt and require this gate to execute successfully on the successor source without weakening any assertion or threshold.
