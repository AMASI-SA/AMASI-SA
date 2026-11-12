# Google and store-courier live acceptance follow-up

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/google-courier-live-acceptance-20261112`.
- Starting production commit: `803e87f72d9661f9b961587bbc0cededfd1f3c90`.
- The user explicitly authorized fixing and publishing the Google dashboard and
  store-courier printing issues, then requested direct print trials on the
  previous affected orders. That authorization remains active.
- State: the preceding release is deployed and verified; actual acceptance
  exposed two bounded gaps. The courier gap is implemented and independently
  verified. The Google display follow-up is being implemented separately.
- This is a work-in-progress checkpoint, not a new release candidate. No new
  source/intent pair is frozen, no follow-up PR is merged, and no second
  publication has been requested.

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

The patch changes one condition in
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

Reviewed helper SHA256:
`29faf5cf37bfc78067616e60d6752c58b13c327d271331dd0d27bab32636f762`.
Reviewed test SHA256:
`86cbf745e9b5e9e8021647e4973b0efdf4725624b274b56ae5b84000656d187e`.

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
will preserve Google metadata and show a positive known amount explicitly as
incomplete in the yellow provider card and the executive Google spend cell.
The executive cell will use the existing same-period platform snapshot and an
optional display-only prop. Financial totals, profit, raw provider spend,
ROAS/CPA, aggregation, account selection and backend source rules remain
unchanged. Unknown/default zero must not become a confirmed or partial spend.

No provider sync, reauthorization, account configuration, ad, budget, financial,
shipment, handoff or customer-state write was used for these completed-order
and saved-dashboard diagnostics.

## Next safe action

Finish the bounded frontend change, tests and independent review; checkpoint
the complete source. Re-read Issue 1006, the current production ref and official
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
