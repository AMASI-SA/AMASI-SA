# Google and store-courier live acceptance follow-up

## Current release checkpoint: adapter passed, publication pending

PR1890 source A `da0d2e2fb82a7848bcd398ca292ba93e1a880681` and intent-only B
`205a040ca557618d5f6f445d7c54592eaf7cec36` merged as
`0af4a3da57072a4995a2ebd0ca76720d17a80ee6`, with the exact B tree. All28
applicable latest B workflows passed, including both Node20/24 adapter
rehearsals; the unrelated TikTok branch-only skip is qualified in
[RELEASE.md](RELEASE.md). The actual shared adapter subsequently passed in
65.79seconds, with exact runtime/source/artifact and isolated package proofs.
All120 unrelated platform files were preserved, and the official guard was
inactive. Publication and real courier/Google acceptance remain open. This
record is maintained on the documentation-only evidence branch, never used
as the deployment input; the immutable source checkpoint below is historical.

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/google-courier-live-acceptance-20261112`.
- Starting production commit: `803e87f72d9661f9b961587bbc0cededfd1f3c90`.
- The user explicitly authorized fixing and publishing the Google dashboard and
  store-courier printing issues, then requested direct print trials on the
  previous affected orders. That authorization remains active.
- State: the preceding release is deployed and verified. The complete native
  data review exposed two courier formatting gaps and a Google display gap.
  The bounded source changes are implemented, tested and independently
  reviewed. This source checkpoint still requires exact CI, the governed build
  and a fresh intent-only B before publication.
- Follow-up Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1890.
- Earlier recoverable WIP: `b20f308a46070e3bbd50a02be27088c46b1e04a2`.
  The final source SHA is pinned in PR1890 and Issue1006 after checkpointing.
- No new source/intent pair is frozen, no follow-up PR is merged, and no second
  publication has been requested. Source readiness is not live acceptance.

## Verified preceding release

PR [1887](https://github.com/AMASI-SA/AMASI-SA/pull/1887) merged source A
`b01341f95f1a1caf3a8741de09f52cb52f6536d9` and intent-only B
`b237fca03fb22e627ab5451b2c14c0f1a17f42b5` as the production commit above.
The merge tree equals B, `ac5225a4cb70b150ee8383305fb5b5da3c3a8a25`.

The authorized single publication completed with the newer green UI wording
`Deployment finished Nov 12, 02:53 AM` (Asia/Riyadh) and published version
`0ef7c77`, replacing `7577839`. The current UI did not literally display the
older wording `Deployment Succeeded`.

The official production guard subsequently returned `verified:true`, `checks:3`,
the exact source A and deployment merge above, runtime
`rg5-e71b3c05b2a27483d7bea11daf03c82f7b80f67e4b9282b5ceb02664a9c95710`,
and boot identity `2026-11-11T23:53:25.260197+00:00`. The governed frontend
artifact tree was
`ab6e7d8994e8a5e32bb2843225bd156c34996798bb1ee780a5643ee35c535325`.
The three official probes verified runtime identity, critical source hashes,
frontend proof and public bytes, including canonical/cache-busted JSON bytes
and MIME for `build-meta.json`. The following official status was `active:false`.
The matching lease is closed. No other task's lease was changed.

## Courier gap found by the live trial

The refreshed historical label reached the new recovery path but returned the
shared Arabic failure message. Fresh, complete provider reads for both
reported examples contained `external_company_name: ""`. The authoritative
city courier remained populated, with the same single active child and native
history. The helper considered every non-null optional alias populated, then
its strict text validator rejected the empty string before checking history
or the destination.

The first patch changes one condition in
`backend/order_engine/store_courier_history_recovery.py`: optional aliases are
absent only when missing, `None`, or exactly `""`. Whitespace, control
characters, non-text values, a different city courier, and external carrier
names still fail. Required carrier identity, numeric IDs, native complete
history, recipient/address, provider rereads, local source/workflow and inbox
fences are unchanged. No archived shipment is reactivated.

Forty new synthetic HTTP-route cases cover the native empty alias shape on
both `refresh` and `complete-and-refresh`, absent/null/matching aliases and
strict negative controls. Success still requires an ID-free order QR, two
complete provider lists, zero provider POSTs and unchanged canonical source and
superseded IDs. No real order, shipment, customer or account identifier is in
the fixtures or this record.

Verification:

- Before the source fix: targeted tests **5 failed, 37 passed, 42 deselected**,
  exit 1; failures were `409 courier_history_unproven` for empty aliases.
- After the fix: the same selection **42 passed, 42 deselected**, exit 0.
- Complete history and race suites: **102 passed, zero skipped**, exit 0,
  6.78 seconds. Independent review reran them: **102 passed, zero skipped**,
  exit 0, 6.89 seconds; no remaining blocker.
- Generic extractor, generic recovery and explicit completion integration:
  **58 passed, zero skipped**, exit 0, 4.47 seconds.
- `git diff --check` passed.

Commands, with the existing isolated Python test environment:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto backend/tests/test_courier_history_identity_recovery.py backend/tests/test_courier_history_recovery_races.py
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto backend/tests/test_salla_generic_shipping_mode.py backend/tests/test_courier_generic_identity_recovery.py backend/tests/test_courier_explicit_completion_recovery.py
```

The subsequent complete native-shape check, before freezing the new release,
found outer ASCII spaces in native activity actor display names. Both reported
orders had nonempty names and valid numeric actor IDs; their names' padding
failed the shared strict text check. Only the actor display-name boundary now
checks string type and raw length at most 300, strips outer ASCII spaces, and
applies the unchanged strict text validator. Other whitespace, control/format
characters, invalid actor IDs, and padded carrier/recipient/transition text
remain rejected. No history or saved source is rewritten.

Thirty additional actual-route cases prove this second gap: before the change,
**6 failed, 24 passed, 84 deselected**, exit 1; after it, the same selection
**30 passed, 84 deselected**, exit 0. Complete history/race tests now pass
**132 cases, zero skipped**, exit 0, 9.09 seconds. A separate independent
reviewer reran all 132: exit 0, zero skipped, 9.21 seconds, with no blocker.
Positive cases combine both actual formatting shapes and exercise both
refresh endpoints with zero provider POSTs, unchanged source/tombstones and
two complete child-list reads.

Root also confirmed both production local records remain completed, have the
same generic/single-archive cohort and no cancellation/pending G47 flags. Fresh
provider order, child and history objects were unchanged across repeated
reads; only sanitized field-shape and equality evidence was retained. Both
pre-follow-up UI attempts still fail, so no preview success is implied.

Final reviewed helper SHA256:
`30dcee74f03fc684ed2824eef60cf2406da0d66f206e5bc51c6bfb187eef59bf`.
Final reviewed test SHA256:
`28032943306ceb110a3f2178c9d89d0b19ccfc720ebc0dc9b8b3b03ff542526e`.

## Google gap found by the same-period UI comparison

The live November 11 saved-spend API retained a positive known subtotal but
returned `total_sar:null`, `amount_available:false` and
`google_native_account_day_missing` for three configured accounts. Existing
saved sync logs, read without starting a sync, showed one complete account,
one needing reauthorization and one manager requiring client selection. The
missing accounts cannot be silently removed or treated as observed zero.

The backend financial result is therefore intentionally incomplete. The UI
normalizer currently preserves known-subtotal metadata only for Snapchat, and
both Google surfaces hide the positive saved amount. The bounded follow-up
preserves Google metadata and shows a positive known amount explicitly as
incomplete in the yellow provider card and the executive Google spend cell.
The executive cell will use the existing same-period platform snapshot and an
optional display-only prop. Financial totals, profit, raw provider spend,
ROAS/CPA, aggregation, account selection and backend source rules remain
unchanged. Unknown/default zero must not become a confirmed or partial spend.

The change is limited to four frontend runtime files:

- `frontend/src/services/dashboardAdsSpend.js` preserves Google quality and
  known-subtotal metadata and exposes a display-only positive amount helper.
- `frontend/src/components/DashboardAdsSpendCard.jsx` uses it only for an
  explicitly null Google full total and the selected matching date range.
- `frontend/src/pages/AdvancedDashboard.jsx` passes optional Google evidence
  from the existing platform snapshot only when both selected dates match.
- `frontend/src/components/AdsExecutiveBreakdownTable.jsx` annotates only the
  Google spend cell when canonical `spend_sar` is explicitly null.

Display requires typed `connected:true`, `amount_available:false`, explicit
incomplete quality/state, and a finite positive known amount. Proven complete
zero keeps the normal numeric path. Missing/invalid/default-zero evidence,
absent selected dates, and mismatched snapshots cannot produce the annotation.
`buildAdsExecutiveMetricRows`, the platform-spend merge function, and all
backend financial or source code are unchanged by this Google follow-up.

Frontend red/green: **14 failed, 32 selected passed** before implementation;
the same selection then passed **46**, with 105 other tests deliberately
deselected by the focused name filter. Fresh complete focused suites passed
**151 tests in four suites**, zero skipped, exit 0, 8.644 seconds. An independent
reviewer repeated these four suites: **151 passed, zero skipped**, exit 0,
with no blocker. The existing card-refresh, cart-freeze and platform-merge
suites also passed **28 tests in three suites**, zero skipped. Total
implementation verification: **179 tests in seven suites**. No runtime source
changed after these runs.

```bash
CI=true PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --no-cache --runTestsByPath src/services/dashboardAdsSpend.test.js src/components/DashboardAdsSpendCard.test.jsx src/components/ProfitSummaryCard.adsExecutive.test.jsx src/pages/AdvancedDashboard.latestOrders.test.jsx
```

The tests check the real response shape using synthetic financial fixtures,
normalization, both visible spend cells, unchanged/null totals and metrics,
full-zero/full-value precedence, and stale or absent period rejection. The
local governed Node22.23.2/Yarn1.22.22 toolchain is warm-verified, with an
independent ignored dependency copy matching the existing package/lock files.

No provider sync, reauthorization, account configuration, ad, budget, financial,
shipment, handoff or customer-state write was used for these completed-order
and saved-dashboard diagnostics.

## Next safe action

Checkpoint the complete reviewed source. Re-read Issue 1006, the current production ref and official
guard before selecting a new source base. Financial PR1889 and Android work
remain separate; preserve any newer production commits. Freeze a fresh
protocol-v5 A/B pair, pass its exact required gates and actual adapter, then
perform one authorized publication with fresh prepare/prepublish. Verify the
newer explicit completion and three matching probes, confirm inactive status,
and repeat both real Mezan previews and the November 11 Google comparison.

The original PR1887 pair is immutable and must not be reused for this delta.
The separate documentation-only evidence branch is never a deployment
candidate. Production changed by this follow-up: **no**. Live acceptance is
still **open**; no successful preview or physical-paper printing is claimed.
