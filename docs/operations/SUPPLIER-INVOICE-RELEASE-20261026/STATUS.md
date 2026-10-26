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
