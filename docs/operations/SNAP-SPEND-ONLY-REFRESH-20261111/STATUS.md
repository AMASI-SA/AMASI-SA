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

Implementation and targeted validation are complete; final GitHub/release gates remain pending. Production changed: **no** for this task. Recoverable WIP checkpoint: `d78290f895fd2ab30db694fa4a317bfa1efb9548`.

- The dedicated manual route now freezes enabled account identities and the exact Riyadh period; durable admission/worker claims prevent duplicate work. It fetches only account HOUR spend, validates every returned hour, and publishes immutable per-account observations behind terminal job evidence. HTTP400 cannot trigger a campaign fallback. Runtime/request budgets and seven-day retention bound the job/observation storage; presentation accepts observations for at most24hours and labels those older than2hours.
- The unified dashboard reader consumes the same validated observations with the existing account FX authority. Display remains provisional; canonical financial/hierarchy facts and close proofs are unchanged. Newer canonical data supersedes only when every day and every source run are display-ready. An independent review found an unreconciled-canonical fallback defect; the full reader readiness gate and permanent regressions now cover it.
- The frontend verifies spend-only job scope, exact dates, all requested account results, and genuinely newer dashboard observation times before success. Partial failures are explicit. Unchanged numeric spend can legitimately succeed; stale readback cannot.
- Root frontend verification: eight affected suites,132unique tests passed (refresh service/component, main card, shared cache, normalization, executive-spend merge/interceptor and dashboard order loading), all invocations exit0. The final19-test refresh rerun includes finite15second POST/status transport timeouts, the six-minute overall observation deadline and no automatic POST retry.
- Independent managed Mongo7 verification:41tests passed,0skipped, exit0 in5.58seconds, using a disposable loopback database and fake provider HTTP boundary. This includes15 real producer-to-dashboard cases plus26 manual route/producer cases. Coverage includes a revision-fenced abandoned-worker recovery interleaving, DST and disabling an account during a fetch. No live provider or production database was used.
- Existing affected backend reader/operational regressions:87passed, exit0 in2.51seconds. Combined targeted backend coverage is128passing tests.
- Independent implementation review found two concrete issues (unreconciled canonical supersession and an unbounded stalled frontend request); both are fixed and covered. The current review reports no additional material scope/security issue. The final verdict will be bound to the exact candidate SHA in the continuation ledger.
- Command: validation venv Python with `PYTHONDONTWRITEBYTECODE=1`, new-worktree `PYTHONPATH`, disposable `ADS_LEASE_TEST_MONGO_PORT`, `-m pytest -p snapchat_v2_test_bootstrap backend/tests/test_snapchat_reporting_v2_manual_spend_dashboard.py backend/tests/test_snapchat_manual_spend_refresh.py -q --tb=short`. The harness stops Mongo and cleans its temporary database.

Local isolated worktree is based on freshly fetched production. Another conversation has been releasing supplier-receiving PR1882. Before any shared `/app` change or rehearsal, check the latest production ref and Release Guard with `python -B scripts/production_release_guard.py status`; proceed only with `active:false`. Preserve unrelated work. No lease or deployment was started for this task.

## Frozen release checkpoint

- PR#1883 is ready for review. SourceA: `dcde835e08dbf7c0fe3acb5d2bbe92e7b05588f3`; intent-onlyB: `a908fb0cff5f9f74b90cfbec03a21295a7cceb6e`; Btree: `6089891dbfe1dfb332128d231ef78372496a7d8e`.
- Runtime identity: `rg5-2592df843af84da961459fe1f158ae677cb5ecd0173cde83483c97372af8070d`. SourcebaseJ remains `4234a88a5e01ce179c5d5711ad80107ecd208a88`.
- Governed local two-build proof passed. The frozen intent exactly matches the sourceA CI artifact from run43070388891/artifact13878356149, SHA256`192235e4e262aee85b4df6d1528d61ef3f139d675060b89f49457fd86922a1fd`.
- Exact-A independent review has no material blockers. Exact-B GitHub jobs and both host adapter rehearsals are in progress; no merge, own lease or publication yet.
- The shared Release Guard was freshly observed inactive after the supplierR2 verifier finished. A read-only development-database scope check found zero active Snapchat owners, so it made no provider request and wrote no report facts; live account acceptance remains a post-publication check.
- This evidence branch is documentation only and must never be deployed. FrozenA/B are unchanged.

## Next safe action

Require exact-B CI and adapter rehearsals, fresh production/guard checks, normal merge, authorized shared adapter build and prepare/prepublish/publication, then verify3 and live spend-only readback.
