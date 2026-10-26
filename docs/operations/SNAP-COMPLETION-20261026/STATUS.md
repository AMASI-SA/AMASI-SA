# Snapchat full management continuation — 2026-10-26

Repository: AMASI-SA/AMASI-SA. Task branch: codex/snap-completion-20261027. Draft PR: #1548. Base: 3a23b587a218f2b190cf1913e3b0b1d22c175cbd, including supplier/purchase #1547. Recoverable initial checkpoint: e7074aef47aee06d9441603677f8330c7a2a7ac6. This document and PR carry continuation evidence because prior status reports Issue #1006 reached its comment limit.

Root coordinates four isolated worktrees and alone owns integration/publication. No advertising writes, budget changes, new grants, campaign activation, sync, or production database writes were performed. No shared deployment lease was acquired or released. Production changed by this task: no.

## Implemented source

| Capability | Source evidence | Verification/deployment state |
| --- | --- | --- |
| File bytes → media → creative → ad, direct ACTIVE publication | Existing upload/publication orchestrator retained; ACTIVE remains explicit approved publication behavior | CI publication backend (including Mongo7) and frontend tests/build passed at initial checkpoint; no live ad creation tested |
| Selected CTA | SHOP_NOW/ORDER_NOW in form, immutable plan, dependency binding, readback, retry conflict; legacy SHOP_NOW plans retained | Integrated backend regression passed; initial-checkpoint publication frontend CI passed; not published |
| Creative content update | creative.update uses existing gateway, editable fields only, explicit all-linked-ads acknowledgement, complete affected-ad catalog, pretransport drift checks, readback/audit/retry/rollback | Integrated backend passed; initial-checkpoint frontend CI passed; not published |
| Campaign/group/ad status, daily budget and bid | Existing deterministic management paths retained | Existing affected management regressions passed; no real financial mutation tested |
| Truly empty account | Complete empty campaign/group/ad catalogs plus complete empty account DAY response; committed account/currency/timezone/period/attribution/producing-run proof, no synthetic facts | Local regression passed; not published |
| Empty provenance | Same proof validator and exact producing runs; bounded projection, no private proof/metrics exposed | Local passed; actual Mongo projection awaits CI service |
| Profit-aware scale | Exact campaign attribution and complete matching cost/cohort/SAR arithmetic; child uses campaign parent; provider freshness separated from commerce freshness | Local regression passed; upstream commerce freshness deliberately blocks scale; not published |
| Bounded automatic decisions | Existing opt-in per-account owner policy, pause/reduce/scale allowlist, limits/expiry/cooldown/audit; no policy enabled here | Existing autonomy tests retained; only pause/reduce can pass without upstream scale proof; wider create/resume/creative experimentation remain manual |

## Material verification

Fresh initial integrated command (backend, lightweight bootstrap): publication, media, management, management hardening/safety, unified execution evidence, empty hierarchy. Result: 354 passed, exit 0, 4.45s, three inherited warnings.

Initial-checkpoint GitHub publication workflow 40379630479 passed: frontend tests and build, backend with Mongo7. Autonomy workflow 40379630502 passed. Reporting workflow 40379630487 found a missing-db regression in the old reader fixture (1 failed/502 passed): the new empty-proof lookup attempted db=None. Corrected to return unproven when no DB exists; original regression is rerun, not waived. Settings scope gate failed on new exact paths; allowlist updated narrowly for this task, preserving fail-closed behavior.

Local Mongo7 binary starts fail with exit100 `open: Operation not permitted` in both workspace and /tmp. No production MONGO_URL is used; real Mongo tests must run on the existing disposable CI services. Local frozen frontend install has remained in package-fetch phase; use CI evidence and do not call a pending local install successful.

## Remaining barriers

1. Public Profile 403 is not proven to have one cause. `public_profile_sharing_read` is an internal capability label, not a documented OAuth scope. Inspect actual token scope, app allowlisting and linked-profile role/sharing. Official Public Profile API scope is snapchat-profile-api; app access is allowlist-only. Existing configurable OAuth scopes permit an explicit operator-approved grant update. Do not widen automatically or claim a scope addition alone fixes this Business API endpoint.
2. A paused account with existing campaigns is not an empty catalog. Its missing hierarchy period evidence remains partial; do not promote zero spend to complete. Mixed empty/nonempty periods remain conservative. Actual Saudi state must be inspected live before claiming its cycle fixed.
3. Unified commerce contracts currently provide no trusted upstream Salla sync/coverage/cost watermark. Provider last_sync_at cannot prove commerce freshness. Scale is explicitly blocked with campaign_commerce_freshness_unproven even if current local calculations are complete. Add a separately reviewed persisted upstream coverage/revision contract before lifting that blocker; never synthesize a timestamp.
4. No true new-ad acceptance with provider media/creative/ad IDs and review state has been performed. Owner has not specified a campaign/budget for a spending test; do not activate one just to test.
5. Emergent project opens from the authenticated home but the project view repeatedly times out on AX/DOM and screenshot; one reload returns home. No paid chat prompt was sent, no arbitrary project URL or hidden API was used. Shared /app and lease are therefore not yet inspected. Do not prepare/publish until access is restored and status is verified.

## Next safe sequence

Finish exact-head affected CI, including actual Mongo projection, inspect combined diff, then source A/fresh v5 intent-only B/isolated official rehearsal. Recheck current remote production and shared `/app` Release Guard status; preserve #1547 and any newer Google/TikTok changes. If another owner has an active lease, wait for that owner. Root alone prepares/prepublishes, publishes exactly once, waits for explicit newer Deployment Succeeded, verifies three probes, requires active=false, then tests actual UI/read behavior. Merge is not deployment.

Live identity freshly observed via GET /api/health: source 07c90edafe14d1263844f9434bbf36d582b4913e; release rg5-dbcb4c97bba2f1eaeea4716ea2cacbf40eaf4f5d0df4fd7dde107a13b7d5d992; verified_identity_available=true. The dashboard visibly preserves independent USD/Saudi spend and reports Saudi hierarchy evidence incomplete. These are read observations, not evidence this task was published.

## Additional integration evidence

Remote checkpoint d916afd8e6266add41b291ecaade382455107b7e passed Reporting V2 (40380256119), actual Mongo provenance (40380256024), settings management (40380256218), autonomy (40380255996), and release readiness (40380256183). Later source follow-ups must pass their own exact-head checks before merge.

Independent review found and corrected stale empty proof surviving populated refresh ($unset and transition regression), affected creative list truncation (explicit fail-closed 200 limit with exact count), and rollback token-await scope drift (final authorizer, confirmed no-send result, MockTransport zero-PATCH regression). Follow-up integrated focused check:269passed/3local-Mongo-skipped, exit0,3.38s. Separate execution/profit gate suite:110passed,exit0,1.40s.

Broad local run:679passed/35skipped/3failed,8.92s. The three failures are hourly publication heartbeat cases refusing admission under host resource pressure (cgroup usage 7,798,558,720/8,589,934,592 bytes). Exact unchanged base3a23b587a reproduces the same3failed/2passed in0.59s. Do not hide this as a green local broad run, alter production governor, or relax thresholds. Reporting CI on d916 passed these cases under its own isolated environment.

Post-completion producer demotion was investigated and rejected: real fail_sync_run filters status=running. New storage-boundary regressions prove mid-publication failure retains valid mixed old/new committed days and completed producers; bookkeeping failure does not invalidate them.
