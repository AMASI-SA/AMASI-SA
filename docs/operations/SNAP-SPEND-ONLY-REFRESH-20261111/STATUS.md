# Snapchat dashboard spend-only refresh — 2026-11-11

## Scope and authorization

The owner requested that the dashboard advertising-expense refresh button fetch Snapchat spend only, for the selected period and enabled accounts, without campaign statistics or other providers. The owner also requested an audit of old Mezan paths and stopping them only if unused. Earlier authorization to merge and publish the performance repairs remains in the conversation. Preparation-file creation remains excluded.

Task branch: `codex/snapchat-dashboard-refresh-v2-20261111`.
Reviewed starting production commit: `4234a88a5e01ce179c5d5711ad80107ecd208a88`.
Starting tree: `d696d29e828044c808778e95a20e48c3fb93f921`.

## Confirmed defect and intended repair

The button currently calls the native legacy refresh while the visible amount is read through the unified reporting V2 reader. A completed legacy job does not prove the displayed amount refreshed.

Implement a bounded asynchronous account-level HOUR stats request with `fields=spend` and no breakdown; split requests into at most seven-day windows. Freeze the authorized enabled account scope and selected Riyadh period. Publish independently validated spend observations in a separate collection, and consume them only as provisional dashboard presentation data using the existing account FX authority. Do not change financial facts, close proofs, reconciliation, campaign metrics, or the two-hour automatic cadence.

Do not invoke the full V2 pipeline, old native refresh, entity discovery, conversions, AI, or other provider refreshes from this button. Completion must reflect per-account provider evidence and matching dashboard readback.

## Legacy audit

The native legacy producer is not unused. It feeds the reachable all-platform Ads Manager and native Snapchat reports. The current Mezan 2 Snapchat entity-settings API and creative metadata enrichment also read the rich legacy entity catalogue. The much older Ads V2 Snapchat connection/sync is already explicitly frozen. No wholesale scheduler disable or data deletion is justified by this audit.

## Implementation ownership

- Backend producer/job: `backend/snapchat_v2/manual_spend.py`, `backend/integrations_control_center/snapchat_manual_spend_refresh.py`, and its existing tests.
- Root: unified dashboard reader, frontend refresh/readback, task documentation and release coordination.
- Independent regression agent: new `backend/tests/test_snapchat_reporting_v2_manual_spend_dashboard.py`.

## Verification and release state

This is an initial WIP checkpoint of the diagnosis and plan. No source fix or new test run is claimed yet. Production changed: **no** for this task.

Local isolated worktree is based on freshly fetched production. Another conversation has been releasing supplier-receiving PR1882. Before any shared `/app` change or rehearsal, check the latest production ref and Release Guard with `python -B scripts/production_release_guard.py status`; proceed only with `active:false`. Preserve unrelated work. No lease or deployment was started for this task.

## Next safe action

Implement and test the producer-to-dashboard chain on a disposable database with an HTTP boundary fake that rejects every non-spend request. Verify both enabled accounts, per-account partial failures, explicit zero/missing data, duplicate admission/claim, date scope, currency, and financial isolation. Obtain independent review, checkpoint verified work, then follow the repository v5 source A / intent-only B release protocol.
