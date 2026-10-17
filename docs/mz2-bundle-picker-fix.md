# MZ2 bundle picker and preview reset fix

Status: implemented and locally verified; not deployed.

Replaces the separate search and native component select with one searchable combobox. Results appear under the input and contain the product main_image, name and SKU, with a missing-image fallback. Uses the existing Product V2 query and IDs; current product remains excluded. Loading, failed search and empty results have explicit states. Mouse and arrow/Enter/Escape interactions are supported.

Preview options now explain their temporary purpose, with per-field clear and clear-all actions. Clearing a parent also prunes hidden dependent selections and text; stale preview totals are invalidated. Bundle definitions and orders are not written by these controls.

Validation: direct react-scripts runner, BundleEditor.test.jsx and productBundles.test.js: 2 suites, 26 tests passed. git diff --check passed. No production changes or business writes. Production remains c03c9df22d07a9a99984b02ab134763b0ae600a8. Next: CI/review and a separately governed release with a newly frozen v5 intent; do not deploy using the old intent.

## Production verification — 2026-10-17

Supersedes the pre-release status above. PR1412 merged as 7da8b95c5bf79cd001d0714439dc5b7f89e18f31 after all checks passed. Source A4d3098f1c0ad7b5d66c580b7958c5bdcc67b2256; intent B5ae1b41895bee8239687c76adf4ca659e9a9e57f. Reviewed readiness38982546038 passed including clean-clone adapter rehearsal.

Actual /app governed rehearsal exit0 (61.48s), prepare and immediate prepublish exit0. One publish. Emergent displayed a newer Deployment finished Oct17 02:55AM (current UI wording, not literal Deployment Succeeded), publish hash c5d399a. verify --url https://mezansalla.com exit0, verified=true, checks=3; matching source/deployment above and runtime rg5-71a32f736237284fcfc9f15c1baf761fcbe1a6ee41975eb877a43a91dc37d742. Frontend artifact tree d2ee16a96700dc0e2e9ae53607fd5a2a62e96f8bd30f2c2991c436c19ec4b5a6. Follow-up status active=false.

Production changed YES. No business writes. Generated untracked Python cache archived outside /app; unrelated files preserved. This documentation checkpoint follows deployment and is not part of its runtime identity. No repeat publish required; release slot is free for the next coordinated task.
