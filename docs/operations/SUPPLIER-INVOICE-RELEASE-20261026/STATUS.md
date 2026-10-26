# Combined supplier and purchase release 2026-10-26

Server publication authorized; no financial writer activation, production data mutation or supplier migration. APK belongs to the Android task.

## Source
Production base J: a7103f71307b516f65e3d085c8cbd09a22d7abae. Preserve product PR1539, Google PR1542 and Snapchat PR1545.
Combined source transfers PR1543 source a8733bb7a3b321878a5055d67251cc771055caa7 (configured catalog + numbering) and supplier commit b81c7406e53128ab06631bada57d986743fdf6c4 without duplicating numbering. Previous PR1540/1543 artifacts are invalid for this new source. Task branch: codex/supplier-catalog-combined-20261026. Exact source SHA is the commit containing this record, available in Git history/PR.

Suppliers choose multiple categories and optional services; categorized suppliers accept category products with no services selected. Existing service-only suppliers retain behavior until explicitly categorized. No replacement suppliers needed. Add Supplier crash fixed. Configured purchase catalog tolerates unrelated incomplete legacy variants; purchases retain canonical configuration validation. Automatic owner-scoped invoice numbers preserve duplicate checks and idempotent retries.

## Verification and deployment
Fresh /app status at approximately2026-10-26T17:58UTC returned active=false. No lease, shared workspace change, rehearsal, merge or publication performed by this task. Source cherry-picks applied cleanly. Exact-source CI pending. Prior isolated candidate results (supplier71, invoice/catalog97, frontend12) do not prove this combination.

Next: pass exact-source CI/readiness; validate its candidate artifact and create B changing only release/release-intent-v5.json. Pass B CI and isolated official rehearsal before merging. Recheck remote Production before each boundary. No squash/rebase after A/B freeze. Refresh shared status; preserve Preview at /app/.worktrees/mz2-supplier-editor-fix-20261025 and all untracked files. Update /app by ff-only, build via governed adapter, prepare/prepublish, publish exactly once. Require newer visible Deployment Succeeded, official three-probe verification and active=false.

## Rollback
Before preparing, capture the current verified live source/deployment/release identity and retain its reviewed intent. Rollback uses an inverse of only this task's code on current Production, new reviewed A/B intent/build/gates; preserve unrelated releases. Never reset hard, remove untracked files/test databases, migrate suppliers or reverse real financial data. GitHub base J alone is not proof of the live deployment identity.

Issue1006 is full; prior evidence is on codex/supplier-invoice-handoff-evidence-20261026 at7bfabc1605bd4abf6b94d8061022b5f0464eb197. This is the canonical current task status. Production changed by this task:no.
