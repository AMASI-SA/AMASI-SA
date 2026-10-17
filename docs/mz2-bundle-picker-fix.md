# MZ2 bundle picker and preview reset fix

Status: implemented and locally verified; not deployed.

Replaces the separate search and native component select with one searchable combobox. Results appear under the input and contain the product main_image, name and SKU, with a missing-image fallback. Uses the existing Product V2 query and IDs; current product remains excluded. Loading, failed search and empty results have explicit states. Mouse and arrow/Enter/Escape interactions are supported.

Preview options now explain their temporary purpose, with per-field clear and clear-all actions. Clearing a parent also prunes hidden dependent selections and text; stale preview totals are invalidated. Bundle definitions and orders are not written by these controls.

Validation: direct react-scripts runner, BundleEditor.test.jsx and productBundles.test.js: 2 suites, 26 tests passed. git diff --check passed. No production changes or business writes. Production remains c03c9df22d07a9a99984b02ab134763b0ae600a8. Next: CI/review and a separately governed release with a newly frozen v5 intent; do not deploy using the old intent.
