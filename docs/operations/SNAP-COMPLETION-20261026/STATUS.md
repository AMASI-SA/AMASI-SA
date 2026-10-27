# Current acceptance evidence — 2026-10-27

This section supersedes the release-planning state below. Earlier sections are historical evidence, not the current deployment status.

Reviewed production-branch head: `5c3353034be910ab2bde62c12512d1d8ded26faa` (merge #1581). It includes merged Snapchat #1553, Salla pagination #1572, TikTok #1578 and courier #1581. This documentation checkpoint starts from that exact head and changes no runtime source, release intent or deployment state.

## Merged is distinct from deployed

The coordinator's latest live observation identifies source `c944ff70ae1c5874e2584ec0060944d2d27dcfb5`, corresponding to #1570, release `rg5-0f58cf8071f853f4b7936e51c4e37ce42abb3c500b2015d16f6f4defefa300d7`. PR #1570 records three successful protocol-v5 probes and `active=false`. This evidence review did not issue a new health probe or inspect the shared host/lease; recheck those before any publication.

#1572 is merged into the latest branch, but its publication has no verified completion. Neither its merge nor a later branch head proves it is running. Do not label the latest repository head deployed or claim new Snapchat production acceptance from source tests alone.

## Management acceptance matrix

| Capability | Implementation and fresh evidence | Production acceptance / limit |
| --- | --- | --- |
| Campaign/group/ad creation | Existing deterministic proposal, approval and execution paths; fixed provider endpoints; create controls verified through independent provider reads | General management creates remain PAUSED. The separately approved publication plan creates campaign/group/ad ACTIVE directly; activation flags and fresh server-owned publication proof remain required. No live creation was performed by this review. |
| Selected active launch and CTA | Explicit ACTIVE publication contract; no forced PAUSED-then-activate sequence. SHOP_NOW/ORDER_NOW is bound to the immutable approved plan, retry identity and readback | Configured ACTIVE is distinct from provider review and actual delivery. Public Profile authorization and a separately owner-specified campaign/budget remain prerequisites for a live spending acceptance test. |
| Campaign and group updates | PATCH paths retain account/parent identity, current settings proof, provider drift checks, readback and audit | No live configuration or budget mutation was performed for acceptance. |
| Existing ad update | Name, status and permitted tracking fields are editable. Content uses the separate creative.update path rather than an unsupported ad creative_id change | Editing creative content affects all associated ads and triggers provider re-review. This is not a promise of immediate delivery. |
| Pause/resume | ACTIVE/PAUSED controls, explicit activation acknowledgement and an independent activation kill switch are retained | No real campaign was activated or paused merely to test code. |
| Daily budget and bid | Native micro amounts, current field-specific settings proof, budget limits and independent provider readback are covered | Lifetime budget and lifetime spend-cap updates remain deliberately blocked by snapchat_management_financial_field_unproven until their native settings proof contract exists. Do not advertise them as completed capabilities. |
| Creative shared-impact authorization | Owner acknowledgement, account-bound complete affected-ad catalog, exact count, fail-closed limit of 200 ads, immutable intent fingerprint and pretransport scope revalidation | Larger shared audiences are refused rather than silently truncated. No real creative was changed. |
| Readback, audit and rollback | Submitted values are checked by a separate provider read. Rollback revalidates drift and shared impact, including a final transport authorizer after credential refresh | MockTransport regression proves zero PATCH requests when the shared audience changes during token refresh. Live mutation/readback acceptance is still unverified here. |
| Idempotency and uncertain outcomes | Same-key/different-body conflict, leases, terminal retry deduplication and read-only reconciliation prevent blind repeat writes | Applied-then-timeout, retry and rollback regressions pass. This review issued no provider write. |

Fresh command from `backend`:

```bash
/workspace/scratch/d49e2f8e58c9/test-venv/bin/python -m pytest -p snapchat_v2_test_bootstrap tests/test_snapchat_campaign_management_v1.py tests/test_snapchat_management_execution_hardening_v1.py tests/test_snapchat_management_safety_regressions_v1.py tests/test_snapchat_campaign_publication.py -q --tb=short
```

Result: **178 passed, 0 failed, exit 0, 2.53 seconds**, with three inherited deprecation warnings. The tested management/publication source and corresponding test files were compared byte-for-byte with the reviewed remote head and matched. This is local synthetic-provider regression evidence; it does not substitute for a fresh deployment identity check or real provider mutation acceptance. This documentation update does not rerun Frontend CI, actual Mongo services or a release rehearsal.

## Current follow-up queue

- Saudi coverage is first: stale-base PR #1575 is closed and superseded by Draft PR #1583, source A `edc86001feafa106340096116c8fd583378cd5a9`, based on `5c3353034be910ab2bde62c12512d1d8ded26faa`. Its exact-head CI and fresh protocol-v5 candidate must complete before freezing a new intent-only B. Do not reuse #1575's old-base intent or treat #1583 as merged/published.
- Outcome cost completeness PR #1576 is still a Draft on obsolete base `2d275221ba351dd31fc6d8106cfad3db186f448b` (#1572). Its old source/intent pair is not a current release handoff. Reprepare that change after the Saudi increment on the then-current production branch, preserve intervening changes, and create a fresh source/intent pair. Never deploy the stale pair above newer updates.
- #1572 remains present in the latest repository head but without verified publication. Its pagination fix also does not prove upstream Salla/product-cost freshness; the Snapchat scale blocker campaign_commerce_freshness_unproven remains a separate unresolved contract.

Evidence sources: GitHub PR snapshots #1570, #1572, #1575, #1576 and #1583; the coordinator's live observation; remote Git head and exact file comparisons; the local test command/result above. No shared worktree was modified. No lease, intent, merge, deployment, financial write, provider mutation, campaign activation or budget change is part of this documentation task.

---

# Current release integration — PR #1553

Current branch: codex/snap-completion-release-r2-20261026. Current reviewed base:4ab6e8bc1d39d1a3df555c874ae1278d284529c1, preserving #1552 Salla customer option pricing and #1547 suppliers. Source checkpoint3d9053ca42d26f1f4b59834f7f322224f74a77b7 copies only this task paths over that base; zero overlap with intervening changes. Exact latest source/intent SHAs and CI results are in PR #1553. Old PR #1548 is closed as superseded; its intent6681df33 MUST NOT deploy above the new base. No old intent is reused or edited by hand.

Fresh final integrated local check before base integration:305passed/3local-Mongo-skips, exit0,3.54s. The source A495101 and intent B6681df33 checks were isolated evidence for the previous base; they do not qualify the new release. New current-base exact-head CI, new candidate intent and intent-only B, then Host20 clean-clone rehearsal remain required.

Production changed by this task:no. Another conversation deployed supplier source761a8f330cb3bfcadbd430e6ca9a954008dbe9de with verified release rg5-82da34e8c680c0bb12638192f5adc916a1b0f8a180006dc1b087290d604c97d4. Remote then advanced to #1552. Recheck remote/live/shared lease before any prepare. Emergent home is authenticated; multiple project/terminal attempts timed out, including a fresh home-tab approach (Options offers rename/move/delete only). No paid chat prompt, release lease, provider write, campaign activation, or budget change was performed. Current production acceptance remains blocked; Public Profile authorization and trusted upstream commerce freshness remain unresolved as detailed below.

Historical implementation and verification record follows.

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

## Saudi live finding and request-scope repair

Live read-only account inspection: Saudi has 62 stored campaigns, 0 active, spend 0 SAR complete. Last closed day 25 October: account/campaign complete, groups/ads incomplete; all other displayed readiness checks complete. Public Profile catalog denies access while management role admin and approved mutation/activation gates open. No grant or provider write was performed.

Source now falls back to complete same-run account-bound provider campaign catalogs when DAY campaign response is complete/empty and no DAY parent IDs exist. Sends actual child stats requests; preflights both levels together against existing 500-call limit without trimming parents/days or raising limits. Transient ID list is removed before storing summaries/diagnostics. Child [] responses stay explicitly partial; no zero-performance proof is invented. 62 parents × 1 day × 2 levels = 124 minimum calls; 7 days = 868 must fail preflight. Live one-day read sync and provider responses remain required after publication. No claim Saudi readiness is fixed on production.

Checkpoint 01430aba7a4b53c60b7994ecf3d739a9e046628e passed all 13 listed workflow runs, including CodeQL/Security, affected Snapchat suites, actual Mongo, frontend tests/build, candidate A/B build and candidate intent. New fallback source and CTA explanatory text need fresh CI. Local fallback suite 95 passed/1.86s. No source/intent B pair is frozen yet; never reuse the 01430 candidate for new source bytes.
