# Supplier categories and invoice numbering release, revision 3

Server publication is explicitly authorized. Do not enable financial/material writers. APK work belongs to the other conversation.

## Source and evidence

- Reviewed Production base J: `058e49ca92275678a0c40f97fc09732875cee415` (PR1536); preserves the newer product-creation and TikTok fixes.
- Current branch: `codex/supplier-invoice-release-r3-20261026`.
- Source commits: `b81c7406e` supplier category/service contract, `dd936b05b` automatic operational invoice numbering, `4860c5ca8` invoice test discovery. Original commits and PR1508 attribution retained by cherry-pick.
- Previous candidate PR1528 at `5fad9bc57451f6ea3dc28f746ab8f1acbaf7d596` passed all applicable CI. Its intent artifact is tied to the older base and must not be used for revision 3.
- Prior focused local verification: supplier71, invoice61 with retained synthetic Mongo databases, frontend12 PASS. Application changes remain unchanged; fresh exact-source CI is required on this revision.

Suppliers select multiple categories and optional services restricted to those categories. Categorized suppliers can receive products in their categories even with no selected service. Existing service-only suppliers keep their legacy dispatch behavior until explicitly classified. The Add Supplier modal crash is fixed. Invoice numbers are generated in the existing transaction; legacy payload/idempotency behavior is retained. No live supplier/category migration or financial write is performed.

## Deployment state

No merge, source update in shared `/app`, lease acquisition, or publication by this task. Production runtime is not changed by this task. The user's last `/app` status was `active:false`; refresh before any shared modification or rehearsal. Local terminal can access GitHub, but no direct authenticated shell transport to the Emergent `/app` has been established. Repository deployment documentation requires the existing Emergent publish UI, followed by official verification; GitHub merge is not deployment.

Do not use direct Windows UI Automation helpers: the available Computer Use skill is the supported desktop route and excludes the ChatGPT desktop UI. A previous Computer Use attempt was stopped by the user's Escape key; no publish occurred. The user subsequently requested continuing from our terminal. Preserve all user/other-conversation terminal sessions, Preview runtime/DB/session, and untracked files.

Issue1006 cannot accept new comments due to GitHub's 2500-comment limit. This status and the PR are the durable continuation record.

Next: publish this source checkpoint and pass exact-source CI; freeze only its matching reviewed intent as commit B; pass reviewed clean-clone checks; merge without rewriting A/B after refreshing Production. Then obtain fresh shared lease status, follow the official adapter/prepare/prepublish sequence, publish in the existing project once, and require newer visible Deployment Succeeded plus official three-probe verification and inactive lease. Do not claim deployment before these proofs.

## Handoff inspection 2026-10-26: blocked by another release lease

Source remains eeba80e599ed5a8e5d131d1d600c0c2264c30cdb on codex/supplier-invoice-release-r3-20261026; PR1540 remains Draft/unmerged. This evidence branch is not a deployment candidate.

Fresh git fetch succeeded: Production advanced to 56560cf68eb08fa8ef5de9780701722c283fbc95 (PR1539), adding the Salla single-choice radio correction and its tests/intent. Preserve this correction in the next source A. The former A artifact from run40351197036 cannot govern that combined source. No B was created.

Browser access to the supplied code-server URL succeeded. Executed from /app: python scripts/production_release_guard.py status, exit0. Fresh result active=true, protocol_version5, actor codex-tiktok-runtime-csp-20261026, prepared_at2026-10-26T15:45:38.239152+00:00; source99610cfcc223d83c3990f80a2dc4a0024df976ad; deployment058e49ca92275678a0c40f97fc09732875cee415; release_id rg5-c13a5be42076761d91ad1dbee32a2839052f891a92d611dcfd7b5a0a99b07060. This is another conversation's active preparation, NOT proof of successful deployment or a verified rollback identity. No abort/verify/prepare/prepublish was run by this task.

Production changed by this task: no. No shared files, Preview runtime/settings/database/sessions, financial writers, business data, APK, or test databases were changed. No rehearsal, merge, or publication occurred. Repository instructions require active=false before proceeding.

Next safe action: the existing lease owner completes or aborts its exact release. Then re-read /app guard status and refresh GitHub Production. Incorporate the current Production changes into a new source A before freezing an intent-only B; pass fresh exact-source CI, B checks and official isolated rehearsal. Preserve A/B ancestry, merge normally, run the governed /app build and prepare/prepublish, publish once and require newer visible Deployment Succeeded plus three official successful probes and inactive status.

Rollback plan: before the next preparation, capture the then-current verified live source/deployment/release identity and retain its reviewed commit/intent. If this feature later requires rollback, prepare a reviewed inverse source on the latest Production preserving unrelated fixes, generate its new governed intent, and use the same gates. Do not reset the workspace, reuse stale identity, erase databases, or reverse actual financial data. Exact verified prior live identity remains pending because the other publisher owns the current lease.

Issue1006 is at its comment limit; its latest available records were read. This task-specific status is the durable checkpoint. No messages were sent to other users or conversations.

## Resume inspection: active Snapchat publisher

Fresh fetch on user resume returned Production a7103f71307b516f65e3d085c8cbd09a22d7abae (PR1545), preserving Google PR1542 and earlier product PR1539. Current AGENTS.md is unchanged. Preserve all these changes in the future combined source.

Fresh /app status succeeded: active=true; actor codex-snap1545; prepared_at2026-10-26T17:23:40.639077+00:00; source07c90edafe14d1263844f9434bbf36d582b4913e; deploymenta7103f71307b516f65e3d085c8cbd09a22d7abae; release_id rg5-dbcb4c97bba2f1eaeea4716ea2cacbf40eaf4f5d0df4fd7dde107a13b7d5d992. The earlier products/TikTok lease observations are historical. No lease was modified by this task; no rehearsal or shared workspace mutation occurred.

Android coordination confirmed this task owns the future combined server release, Android owns APK/ADB only. PR1543 is still Draft/unmerged at a8733bb7a3b321878a5055d67251cc771055caa7, adding configured purchase catalog support and numbering. Integrate that catalog fix with supplier source from PR1540 and numbering once, on refreshed Production; generate a new artifact, not either old PR artifact. Android reports97Mongo tests and run40358221224 passed; these are prior candidate evidence, not verification of the future combination.

No source A/B was created in this resume and Production changed by this task:no. Next prerequisite remains a fresh active=false from /app after the lease owner finishes, followed by new combined source/CI/intent/rehearsal and all official release gates. Do not abort or verify another publisher's lease. Rollback baseline must be captured from the then-current verified live release before our own prepare.

## Combined Source A accepted, official Linux intent validation pending
PR1547 source761a8f330cb3bfcadbd430e6ca9a954008dbe9de onbasea7103f71307b516f65e3d085c8cbd09a22d7abae has23SUCCESS/6nonapplicableSKIPPED. Readiness40373820461SUCCESS. Artifact12672119936 is exact-source candidate, downloaded via gh after connector/internal and initial TLS failures. Copy retained in evidence/candidate-761a8f330.json. Windows full adapter validation fails POSIX permissions (.env0666); this is not accepted as successful validation. Linux isolated source clone /tmp/supplier1547-rehearsal-761a8f330 exists, sourceAcheckedout. Remoteghunauthenticated; candidate transported through this evidence branch with Git, then official validation required before B.

Fresh/appguardactivefalse approximately17:58UTC and again during preparation. /appHEADa7103f713, tracked clean, untracked.emergent/testing/anddeployer-agent-docs/preserved. No lease, merge or publication. Live priorhealth captured read-only in evidence/prior-production-health.json: source07c90edafe14d1263844f9434bbf36d582b4913e,release rg5-dbcb4c97bba2f1eaeea4716ea2cacbf40eaf4f5d0df4fd7dde107a13b7d5d992, identity/critical/frontendverifiedtrue. EmergentcurrentprojectSalla-Analytics/domainmezansalla.com visible, previous publish36da017, DeploymentfinishedOct26,08:46PM. User signed in. Preserve prior base/intent asrollbackreference; inverse task code only onlatestproduction withfreshA/Bbuildandgates, noDBrollback/reset.

Source/runtime branch codex/supplier-catalog-combined-20261026 is separate from this evidence branch. Do not deploy evidence. Next officialLinuxcandidatevalidation, intent-onlyB,pushCI/isolatedadapter,normalmerge,sharedadapter/prepare/prepublish/UIpublish/verify3/statusinactive. Financial/materialwritesremainoff,Previewuntouched,APKownedbyAndroidtask. Productionchangedbythistask:no.

## Intent B frozen after official Linux validation
SourceA761a8f330cb3bfcadbd430e6ca9a954008dbe9de accepted23SUCCESS/6SKIPPED. Exact artifact JSON SHA256 fbccba8f7b2d90738573771eb844585d5dba85bf88b236d669e141e43976682a. Independent Git source manifest/fullFrontendbytecomparisonPASS. Officialload_candidate_release_intentPASS oncleanLinuxclone withPYTHONDONTWRITEBYTECODE=1. Initialclonevalidationgeneratedbytecodeandwasrejected; newcleanclonevalidatedwithoutweakeningguard. Runtime rg5-82da34e8c680c0bb12638192f5adc916a1b0f8a180006dc1b087290d604c97d4.
Intent-onlyBecb46ca525a76a53fe27158c60d8ac410152b31d pushedPR1547; A..Bonlyrelease/release-intent-v5.json. BCIpending. Isolatedofficialadaptercommand launched in /tmp/supplier1547-clean-761a8f330/frontend with exact production public origin, logs /tmp/supplier1547-adapter.log; command rechecks sharedguardinactivebeforebuild. No /appcodechange/lease/merge/publication. Next BCIandactualadapterresult,thenrefreshProduction/merge/ffbuild/prepare/prepublish/UI/verify3/statusinactive. Preserve A/B unchanged.

## B official rehearsals PASS
Readiness40375153435 for exactBecb46ca525a76a53fe27158c60d8ac410152b31d completedSUCCESS, including HostNode20 clean-clone rehearsal and exact JSON routes. Downloaded sanitized evidence retained under evidence/B-clean-clone. Backendisolatedhealthverifiedtrue, no.gitorsiblingfrontend; runtime rg5-82da34e8c680c0bb12638192f5adc916a1b0f8a180006dc1b087290d604c97d4, source761a8f330cb3bfcadbd430e6ca9a954008dbe9de. ActualEmergent isolatedadapteralsoexited0in51.64s, frontendpackageisolated_runtime_artifact_verifiedtrue; worktreeclean, ignoredidentityexact. /appnotupdated; nolease/publication. LastpendingBcheckCodeQLPython. Awaitallapplicablechecks, refreshbaseandguard, thennormalmergewithoutrewritingA/B.
