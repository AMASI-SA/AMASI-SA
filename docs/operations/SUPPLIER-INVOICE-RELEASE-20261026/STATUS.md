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
